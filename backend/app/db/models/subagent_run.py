"""Sub-agent run state machine + heartbeat row (M2.5.1).

Every time a parent agent spawns a child via the
:func:`build_subagent_capability` capability we register one row here so
the reaper cron, retry-budget bookkeeping, and the hallucination gate
all share a durable spine. The roadmap design principle 6 ("subagent
must not silently hang") is enforced by combining a 30-second
heartbeat from the capability lifecycle hook with a 60-second
:func:`reap_zombies` sweep that flips ``state=ZOMBIE`` on rows whose
``last_heartbeat_at`` has fallen more than five minutes behind.

State machine (terminal states are ``COMPLETED`` / ``KILLED`` /
``FAILED`` / ``ZOMBIE``)::

    PENDING ──> RUNNING ──> COMPLETED
                  │
                  ├──> HALLUCINATION_REVIEW ──> COMPLETED  (admin approved)
                  │                          ╲─> KILLED   (admin rejected
                  │                                        / TTL expired)
                  ├──> ZOMBIE   (reaper sweep)
                  ├──> KILLED   (admin / parent cancel)
                  └──> FAILED   (child raised in-loop)

Indices cover the three hot lookups: per-parent fan-in
(``parent_run_id``), uniqueness on the child's run_id (so the lifecycle
hook can ``ON CONFLICT DO UPDATE`` safely), the reaper's
``state + last_heartbeat_at`` scan, and the workspace-scope filter
inherited from :class:`WorkspaceScopedMixin`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SubAgentRunState(StrEnum):
    """Lifecycle state of one ``SubAgentRun`` row.

    ``HALLUCINATION_REVIEW`` is the bridge to the M2.5 dispatch handler:
    a child's final output failed the aux LLM evidence gate, an
    Approval row is open, and the parent is waiting for an admin
    decision before either accepting the result (→ ``COMPLETED``) or
    cancelling the child (→ ``KILLED``).
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ZOMBIE = "zombie"
    KILLED = "killed"
    FAILED = "failed"
    HALLUCINATION_REVIEW = "hallucination_review"


# Truncate cap for the persisted ``final_output`` column. The full body
# already lives in the child's session_artifact row; here we keep just
# enough to render in the approval card preview without bloating the
# table.
FINAL_OUTPUT_MAX_CHARS = 4096


class SubAgentRun(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "subagent_runs"
    __table_args__ = (
        Index(
            "ix_subagent_runs_state_heartbeat",
            "state",
            "last_heartbeat_at",
        ),
        Index(
            "ix_subagent_runs_workspace_state",
            "workspace_id",
            "state",
        ),
    )

    parent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    child_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )

    # Optional anchor back into the parent chat session so the M0.10
    # notification fan-out and the M2.5 approval card can deep-link to
    # the originating conversation. Null when the parent run is a
    # background flow / cron tick with no session.
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 0 for a child spawned directly by a user-facing run, 1+ for
    # nested children. Used by the reaper sweep + the future
    # ``max_nesting_depth`` policy gate.
    spawn_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    state: Mapped[SubAgentRunState] = mapped_column(
        Enum(
            SubAgentRunState,
            name="subagent_run_state",
            native_enum=False,
            length=32,
            validate_strings=True,
        ),
        nullable=False,
        default=SubAgentRunState.PENDING,
        server_default=SubAgentRunState.PENDING.value,
    )

    last_heartbeat_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retry_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )

    # Aux-LLM hallucination gate score in 0..1 — null until evaluated.
    # Below the gate threshold (default 0.5) routes the row through
    # ``HALLUCINATION_REVIEW`` instead of completing directly.
    hallucination_score: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # Set by :func:`gate_hallucination_or_approve` when an Approval is
    # filed; SET NULL on approval delete keeps cascade clean even when
    # the admin purges old approvals.
    hallucination_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 4 KB-truncated copy of the child's final assistant output. The
    # full body lives in the child's session_artifact (M0.2); we keep
    # this slice so the approval card preview + the audit summary can
    # render without an extra fetch.
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-form classifier set when the run terminates abnormally —
    # ``timeout`` / ``cancelled`` / ``provider_error`` / etc. Mirrors
    # ``session_artifact.error_kind`` shape so the dashboards can
    # share a column.
    error_kind: Mapped[str | None] = mapped_column(String(80), nullable=True)
