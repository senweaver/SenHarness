"""Persistent skill pack models (V2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SkillPackSource(StrEnum):
    BUNDLED = "bundled"
    WORKSPACE = "workspace"
    IMPORTED = "imported"


class SkillPackState(StrEnum):
    """Concept-level lifecycle state for a SkillPack.

    The state machine is enforced in :mod:`app.services.skill_lifecycle`;
    rows can only move along the edges in ``ALLOWED_TRANSITIONS``. The
    rule that a pinned pack is exempt from any *automatic* transition
    (curator / evolver background flows) lives in the same module —
    column ``pinned`` itself is orthogonal to ``state`` so a pack can
    be ``state=ACTIVE`` and ``pinned=True`` simultaneously, or
    ``state=STALE`` and ``pinned=True`` (the manual unpin step is
    required before any auto sweep can act on it).
    """

    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    STALE = "stale"
    PINNED = "pinned"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    TOMBSTONE = "tombstone"


class SkillPack(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "skill_packs"
    __table_args__ = (
        Index("ix_skill_packs_workspace_slug", "workspace_id", "slug", unique=True),
        Index("ix_skill_packs_state", "state"),
        Index("ix_skill_packs_pinned", "pinned"),
        Index("ix_skill_packs_last_used_at", "last_used_at"),
        Index("ix_skill_packs_content_hash", "content_hash"),
    )

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[SkillPackSource] = mapped_column(
        String(32), default=SkillPackSource.WORKSPACE, nullable=False
    )
    manifest_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── M1.1 lifecycle columns ────────────────────────────────
    # ``native_enum=False`` keeps the column on a portable VARCHAR so we
    # never have to round-trip a Postgres ENUM type (which would force
    # an alembic op for every new value down the road).
    state: Mapped[SkillPackState] = mapped_column(
        SAEnum(SkillPackState, native_enum=False, length=32, name="skill_pack_state"),
        default=SkillPackState.ACTIVE,
        server_default=SkillPackState.ACTIVE.value,
        nullable=False,
    )
    pinned: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    effectiveness_avg: Mapped[float | None] = mapped_column(nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_by_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="SET NULL"),
        nullable=True,
    )
    state_changed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    state_changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class SkillFile(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "skill_files"
    __table_args__ = (Index("ix_skill_files_pack_path", "skill_pack_id", "path", unique=True),)

    skill_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class AgentSkill(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "agent_skills"
    __table_args__ = (
        Index("ix_agent_skills_agent_pack", "agent_id", "skill_pack_id", unique=True),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
