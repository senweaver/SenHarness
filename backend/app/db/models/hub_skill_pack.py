"""Skill Hub catalog pack (M3.1).

A ``HubSkillPack`` is the federation-side counterpart to
:class:`~app.db.models.skills.SkillPack`: a portable skill that lives
above the workspace layer so multiple tenants can subscribe to the
same authoritative copy. Two scopes coexist in the same table:

* :attr:`HubScope.PLATFORM` — visible to every workspace in the
  deployment. ``tenant_id`` is NULL. Promotion to PLATFORM scope
  requires a platform admin (the workspace-level admin path can
  only file TENANT-scope packs).
* :attr:`HubScope.TENANT` — visible only to workspaces that share the
  pack's ``tenant_id``. ``tenant_id`` is required for this scope.

Tenant identity
---------------

The deployment does not yet ship a dedicated ``tenants`` table. The
M3.1 service layer derives a tenant id from the calling workspace by
falling back to ``workspace.id`` itself when no separate tenant
column exists. When the M5+ tenancy model lands, the helper switches
to reading ``workspace.tenant_id`` without touching this table — the
column is already nullable + indexed for that future filter.

Slug uniqueness
---------------

Slugs are unique inside ``(scope, tenant_id)``. Two different tenants
can both publish ``code-review`` because their tenant ids differ;
the platform scope (where ``tenant_id IS NULL``) gets exactly one
``code-review``. PostgreSQL treats NULL as distinct in UNIQUE
constraints, so the partial-uniqueness of platform slugs is enforced
by an additional unique index in 0053 that uses
``COALESCE(tenant_id, '00...')``.

Tombstoning
-----------

Moving a row to :attr:`HubSkillPackState.TOMBSTONE` is terminal: the
state machine in :mod:`app.services.hub_skill` rejects every
outgoing edge from TOMBSTONE, and the create path on the M3.3 promote
verb consults :func:`app.services.hub_skill.is_hub_slug_tombstoned`
before allowing a freshly-promoted pack to take a previously-used
slug. The slug column itself stays in place so the audit feed still
references the original name.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin


class HubScope(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class HubSkillPackState(StrEnum):
    """Hub-side lifecycle. Narrower than the workspace SkillPack
    machine because hub packs are catalog entries, not workspace
    runtime objects.

    * ``ACTIVE`` — listed in the catalog, available for subscribe + pull.
    * ``DEPRECATED`` — visible but flagged as superseded; subscribers
      keep their pull but no new subscriptions are encouraged.
    * ``ARCHIVED`` — hidden from default catalog listings.
    * ``TOMBSTONE`` — terminal; slug permanently blocked from reuse
      inside the same ``(scope, tenant_id)`` bucket.
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    TOMBSTONE = "tombstone"


HUB_SKILL_PACK_STATE_VALUES: tuple[str, ...] = tuple(
    s.value for s in HubSkillPackState
)
HUB_SCOPE_VALUES: tuple[str, ...] = tuple(s.value for s in HubScope)


class HubSkillPack(UuidPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "hub_skill_packs"

    scope: Mapped[HubScope] = mapped_column(
        SAEnum(HubScope, native_enum=False, length=16, name="hub_scope"),
        default=HubScope.TENANT,
        server_default=HubScope.TENANT.value,
        nullable=False,
        index=True,
    )
    # ``tenant_id`` is intentionally not an FK: the runtime tenant
    # identifier is derived from the workspace today (no dedicated
    # ``tenants`` table) and the column already needs to accept NULL
    # for PLATFORM-scope rows. When the tenancy model lands the FK
    # can be added in a follow-up migration without touching this row
    # shape.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    state: Mapped[HubSkillPackState] = mapped_column(
        SAEnum(
            HubSkillPackState,
            native_enum=False,
            length=32,
            name="hub_skill_pack_state",
        ),
        default=HubSkillPackState.ACTIVE,
        server_default=HubSkillPackState.ACTIVE.value,
        nullable=False,
        index=True,
    )

    # Provenance — populated by the M3.3 promote pipeline. M3.1 keeps
    # them nullable so admin-direct catalog seeds (no source workspace)
    # can land too.
    promoted_from_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    promoted_from_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    promoted_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
        nullable=False,
    )

    __table_args__ = (
        # NULL-distinct uniqueness — fine for TENANT scope (tenant_id
        # never NULL there). PLATFORM scope (tenant_id IS NULL) is
        # covered by the partial unique index defined in the migration
        # using ``COALESCE(tenant_id, ...)``.
        UniqueConstraint(
            "scope",
            "tenant_id",
            "slug",
            name="uq_hub_skill_packs_scope_tenant_slug",
        ),
        Index("ix_hub_skill_packs_scope_state", "scope", "state"),
    )
