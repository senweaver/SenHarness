"""Per-user Agent stars (favorites + pinning for the sidebar 'recent agents' section)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class AgentStar(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_stars"
    __table_args__ = (
        UniqueConstraint("identity_id", "agent_id", name="uq_agent_stars_identity_agent"),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pinned: Mapped[bool] = mapped_column(default=False, nullable=False)
