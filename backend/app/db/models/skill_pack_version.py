"""Immutable SkillPack content snapshots (M1.2).

Every persisted change to a SkillPack body lands as a new row here;
``SkillPack.content_md`` is reduced to a *cache* mirroring the
currently ACTIVE version. Rollback to v3 means flipping which version
row holds ``state == ACTIVE`` — the historical bytes never leave the
table once written.

State machine (see ``app.services.skill_version`` for the enforcement
layer)::

    PROPOSED ─→ VALIDATING ─→ ACCEPTED ─→ ACTIVE ─→ RETIRED
        │           │
        └→ REJECTED ┘  (terminal)

Only one row per ``pack_id`` can hold ``state == ACTIVE`` at a time.
The activate path is responsible for retiring the previous incumbent
inside the same transaction.

Two unique constraints together provide:

* ``(pack_id, version_no)`` — monotonically increasing version
  numbering driven by ``next_version_no()``.
* ``(pack_id, content_hash)`` — bytewise dedup. A second proposal of
  identical content raises 409 (``skill_version.duplicate_content_hash``)
  rather than silently creating a no-op snapshot.

The state machine here is intentionally separate from the
:class:`~app.db.models.skills.SkillPackState` 9-state lifecycle: that
one tracks the *concept* (does the workspace still want this skill?),
this one tracks the *content* (which historical bytes are live?).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SkillPackVersionState(StrEnum):
    """Lifecycle of a content snapshot.

    ``REJECTED`` is terminal. ``RETIRED`` is "previously ACTIVE,
    superseded by another version" — kept around for rollback and
    audit.
    """

    PROPOSED = "proposed"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    RETIRED = "retired"
    REJECTED = "rejected"


# Stable string set re-used by alembic 0044 so the migration body
# doesn't drift from the enum.
SKILL_PACK_VERSION_STATE_VALUES: tuple[str, ...] = tuple(s.value for s in SkillPackVersionState)


class SkillPackVersion(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "skill_pack_versions"
    __table_args__ = (
        UniqueConstraint("pack_id", "version_no", name="uq_skill_pack_versions_pack_no"),
        UniqueConstraint("pack_id", "content_hash", name="uq_skill_pack_versions_pack_hash"),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    files_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[SkillPackVersionState] = mapped_column(
        SAEnum(
            SkillPackVersionState,
            native_enum=False,
            length=32,
            name="skill_pack_version_state",
        ),
        default=SkillPackVersionState.PROPOSED,
        server_default=SkillPackVersionState.PROPOSED.value,
        nullable=False,
        index=True,
    )
    # ``created_by`` is a free-form provenance tag, not an FK. Values:
    # ``user`` (manual edit), ``evolver`` (M2 agent), ``hub_pull``
    # (M3 federation), ``migration`` (0044 backfill).
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    creator_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_run_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_results: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    superseded_by_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_pack_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(nullable=True)
