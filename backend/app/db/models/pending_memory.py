"""Pending memory queue (M0.7) — cache-aware mutation buffer.

Every ``memorize`` / ``skill.write`` invocation defaults to
``effective="next_session"``, which means the mutation lands here as
a ``PENDING`` row instead of mutating the system-prompt-injected
asset table directly. The promote hook (post-FINAL inside the
``_capture_run_artifact`` site) drains ``PENDING`` rows for the just-
finished session and applies them via the relevant target service so
the next run boots with a coherent prompt cache.

Discriminator ``target_table`` lets one queue serve memories *and*
skill packs (M1) without forking the schema. The ``payload`` JSONB
keeps the canonical tuple per kind; the service layer validates each
payload shape before insert because the columns differ across
targets and DB-level constraints would harden too early.

Identity + workspace scope columns let M0.11 retention sweep cascade
on identity / workspace soft-delete; the ``has_table`` guard already
in :data:`app.services.retention.CASCADE_TARGETS` activates the
moment the migration ships.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    WorkspaceScopedMixin,
)


class PendingMemoryTargetTable(StrEnum):
    """Which downstream table the pending row will mutate when promoted."""

    MEMORIES = "memories"
    SKILL_PACKS = "skill_packs"


class PendingMemoryStatus(StrEnum):
    """Lifecycle of a pending row.

    ``PENDING`` → drained by the post-FINAL promote hook (or the
    workspace sweep cron as a backstop). On success → ``PROMOTED``.
    Hard-cap / quota / dedup blockers → ``SKIPPED`` (terminal, audited
    once). Transient apply errors → ``FAILED`` with ``failure_count``
    bumped; the row stays eligible for retry until
    ``failure_count >= max_failure_count_before_skip``, after which the
    sweep flips it to ``SKIPPED`` to bound the queue size.
    """

    PENDING = "pending"
    PROMOTED = "promoted"
    SKIPPED = "skipped"
    FAILED = "failed"


class PendingMemory(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "pending_memories"
    __table_args__ = (
        Index(
            "ix_pending_memories_ws_session_status",
            "workspace_id",
            "session_id",
            "status",
        ),
        Index(
            "ix_pending_memories_ws_status_created_at",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_table: Mapped[PendingMemoryTargetTable] = mapped_column(
        String(40), nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[PendingMemoryStatus] = mapped_column(
        String(16),
        nullable=False,
        default=PendingMemoryStatus.PENDING,
        server_default=PendingMemoryStatus.PENDING.value,
        index=True,
    )
    promoted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    promoted_target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    failure_count: Mapped[int] = mapped_column(
        default=0, server_default="0", nullable=False
    )
