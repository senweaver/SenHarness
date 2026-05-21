"""SessionCheckpoint — named snapshot of a session at a specific message cursor.

Fork / rewind / replay all key off one of these rows. ``message_count`` is
what we copy when we fork; ``snapshot_json`` carries denormalised metadata
(title, agent_id, metadata_json) so a replay without live access to the
original session can still reconstruct context.

M2.5.2 added two columns:

* ``parent_checkpoint_id`` — self-referential link to the snapshot this
  one was forked / rewound from. Lets the GC clear ``snapshot_json``
  bytes on aged rows while keeping the lineage chain intact for the
  rewind / replay UI.
* ``pruned_at`` — timestamped when the daily ``gc_old_checkpoints``
  cron empties ``snapshot_json``. Set means "metadata-only tombstone";
  null means "live snapshot, full payload still present".
"""

from __future__ import annotations

import uuid
from datetime import datetime

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

    parent_checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_checkpoints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    pruned_at: Mapped[datetime | None] = mapped_column(
        nullable=True, index=True
    )
