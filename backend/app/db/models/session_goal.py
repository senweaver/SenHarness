"""Session goal lock + per-message alignment scores (M0.1).

A workspace member can lock a long-running chat session to an explicit
"north star" goal. Each subsequent assistant turn is judged async by an
auxiliary LLM and gets a 0..1 alignment score plus a short rationale; a
score below the per-goal threshold flips ``flagged=True`` so the UI can
surface the drift.

Both tables hold user-authored / LLM-generated text and therefore must
participate in the GDPR cascade soft-delete + physical purge wiring
(M0.11). ``session_goals`` carries the ``SoftDeleteMixin`` directly;
``goal_alignment_scores`` is wiped together with its parent goal (and
again under workspace / identity cascade) — see M0.11 for the actual
purge ARQ task.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SessionGoal(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "session_goals"
    __table_args__ = (
        # Hot lookup: "is there an active goal for this session right now?"
        # Filtering on ``unlocked_at IS NULL AND deleted_at IS NULL`` is
        # cheap with a composite index on ``session_id`` + ``unlocked_at``.
        Index(
            "ix_session_goals_session_active",
            "session_id",
            "unlocked_at",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    goal_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    success_criteria: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    locked_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    locked_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    unlocked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    unlocked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Range 0..1 — assistant messages scoring < threshold flip ``flagged``
    # on the score row and emit the ``goal.alignment_low`` notification.
    alignment_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.6, server_default="0.6"
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class GoalAlignmentScore(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "goal_alignment_scores"
    __table_args__ = (
        Index(
            "ix_goal_alignment_scores_goal_message",
            "session_goal_id",
            "message_id",
        ),
    )

    session_goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-form: e.g. ``"deepseek:deepseek-chat"`` for the aux model that
    # produced this score, or ``"heuristic:fallback"`` when the aux call
    # failed and we degraded to the static 0.5 mean. Persisted so the
    # cost dashboard can split aux vs. fallback later.
    judged_by_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
