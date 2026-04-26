"""SessionCheckpoint — named snapshot of a session at a specific message cursor.

Fork / rewind / replay all key off one of these rows. ``message_count`` is
what we copy when we fork; ``snapshot_json`` carries denormalised metadata
(title, agent_id, metadata_json) so a replay without live access to the
original session can still reconstruct context.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SessionCheckpoint(
    UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "session_checkpoints"
    __table_args__ = (
        Index(
            "ix_session_checkpoints_session_created",
            "session_id",
            "created_at",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)

    snapshot_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
