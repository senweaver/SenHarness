"""Squad — multi-Agent team."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SquadStrategy(StrEnum):
    PLANNER = "planner"
    WORKER_POOL = "worker_pool"
    ROUTER = "router"
    HANDOFF = "handoff"
    DEBATE = "debate"


class Squad(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "squads"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    strategy: Mapped[SquadStrategy] = mapped_column(
        String(32), default=SquadStrategy.ROUTER, nullable=False
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )


class SquadMember(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "squad_members"
    __table_args__ = (
        UniqueConstraint("squad_id", "agent_id", name="uq_squad_members_squad_agent"),
    )

    squad_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("squads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_in_squad: Mapped[str] = mapped_column(String(64), default="member", nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
