"""Immutable hub skill version snapshot (M3.1).

Mirrors the :class:`~app.db.models.skill_pack_version.SkillPackVersion`
shape but lives at the catalog layer so workspaces that subscribe
+ pull a hub pack always land on a frozen, hash-addressable body.

Two unique constraints provide:

* ``(hub_pack_id, version_no)`` — monotonic numbering per pack.
* ``(hub_pack_id, content_hash)`` — bytewise dedup so repeated
  promotions of identical bodies collapse to one row.

``is_active`` is a boolean rather than a state column because the
hub-side machine has no validation/rejection step (sanitization +
admin promotion happen *before* the row lands here, in M3.2 / M3.3).
At most one row per ``hub_pack_id`` should hold ``is_active=true``;
M3.3 ``activate_hub_version`` will enforce that invariant inside a
single transaction in the same way M1.2 does for SkillPackVersion.

``promoted_from_workspace_version_id`` traces the original
:class:`SkillPackVersion` row that supplied this body, so an audit
can reconstruct "which workspace's v7 became hub v3" without
exposing the full source pack.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class HubSkillPackVersion(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "hub_skill_pack_versions"

    hub_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hub_skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    files_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
        nullable=False,
    )
    promoted_from_workspace_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        default=False,
        server_default="false",
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "hub_pack_id",
            "version_no",
            name="uq_hub_skill_pack_versions_pack_no",
        ),
        UniqueConstraint(
            "hub_pack_id",
            "content_hash",
            name="uq_hub_skill_pack_versions_pack_hash",
        ),
        Index(
            "ix_hub_skill_pack_versions_pack_active",
            "hub_pack_id",
            "is_active",
        ),
    )
