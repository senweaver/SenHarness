"""Top-level run lifecycle spine (M2.5.2 Session Lifecycle Resilience).

Every interactive (WebSocket) or non-interactive (channel / flow / batch
case) turn that drives :func:`AgentBackend.run` registers one
:class:`InflightRun` row at startup, bumps ``last_seen_at`` on every
emitted ``RunEvent``, and transitions to a terminal state once the loop
returns (or raises). A dedicated ``pid_token`` column captures the host
+ pid + boot epoch of the worker that owns the row so a backend restart
can reliably distinguish "still running on me" from "abandoned by a dead
process".

Two recovery hooks consume the table:

* :func:`app.services.inflight_run.recover_inflight_runs` — called from
  the FastAPI lifespan on startup. Scans every ``state=RUNNING`` row
  whose ``pid_token`` does not match the current process's token and
  flips it to ``LOST``.
* :func:`app.jobs.inflight_recovery.reap_stale_inflight_runs` — every
  five minutes. Catches rows whose ``last_seen_at`` has fallen 15+ min
  behind even within a still-running process (the worker is probably
  hung on an upstream call).

The table deliberately does NOT overlap with :class:`SubAgentRun`
(M2.5.1): subagent_runs spans **child** runs spawned by the planner /
batch capability and lives next to the hallucination gate, while
inflight_runs spans **top-level** runs and lives next to the recovery
notification path.

Indices cover the four hot paths:

* ``ix_inflight_runs_state_pid_token`` — startup recovery sweep
  (``WHERE state='running'`` filtered then by pid_token mismatch).
* ``ix_inflight_runs_state_last_seen_at`` — 5-min cron sweep
  (``WHERE state='running' AND last_seen_at < now-15min``).
* ``ix_inflight_runs_session_id`` — WS reconnect helper
  (``list_lost_for_session``).
* ``run_id`` unique — the lifecycle hook can ``ON CONFLICT`` safely
  when a re-attached client replays a register call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class InflightRunState(StrEnum):
    """Lifecycle state of one ``InflightRun`` row.

    Terminal states: ``COMPLETED`` / ``LOST`` / ``CANCELLED`` / ``FAILED``.
    ``PAUSED`` is reserved for an approval-blocked run that hasn't been
    abandoned yet — the spec keeps it in case a future iteration wires
    HITL pauses through this table.
    """

    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    LOST = "lost"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Cap for the persisted ``error_kind`` short classifier. Mirrors the
# ``session_artifact.error_kind`` shape so dashboards can share a column.
ERROR_KIND_MAX_CHARS = 80

# Cap for ``pid_token`` payload (``host:pid:start_seconds``). Hostname
# is the long part — RFC 1035 maxes at 253 bytes; we truncate to 80 so
# the column stays cheap and the prefix-equality lookup stays fast.
PID_TOKEN_MAX_CHARS = 80


class InflightRun(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "inflight_runs"
    __table_args__ = (
        Index(
            "ix_inflight_runs_state_pid_token",
            "state",
            "pid_token",
        ),
        Index(
            "ix_inflight_runs_state_last_seen_at",
            "state",
            "last_seen_at",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    backend_kind: Mapped[str] = mapped_column(String(40), nullable=False)

    # Trimmed snapshot of the originating ``RunRequest`` minus secrets and
    # raw attachment bytes. Used for diagnostic display and (eventually)
    # the ``/retry`` reissue path. Never round-trip secrets / bytes here.
    request_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    last_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    state: Mapped[InflightRunState] = mapped_column(
        Enum(
            InflightRunState,
            name="inflight_run_state",
            native_enum=False,
            length=32,
            validate_strings=True,
        ),
        nullable=False,
        default=InflightRunState.RUNNING,
        server_default=InflightRunState.RUNNING.value,
        index=True,
    )

    # ``host:pid:start_seconds`` — the ``recover_inflight_runs`` sweep
    # treats any token that doesn't equal the current process's prefix
    # as belonging to a previous backend incarnation.
    pid_token: Mapped[str | None] = mapped_column(
        String(PID_TOKEN_MAX_CHARS), nullable=True
    )

    started_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    error_kind: Mapped[str | None] = mapped_column(
        String(ERROR_KIND_MAX_CHARS), nullable=True
    )

    # Live runtime probes — set by the native runner so the Agent View
    # cards can render the active phase / tool without scraping events.
    # NULL between transitions; cleared when the run terminates.
    current_phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    running_tool_name: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
