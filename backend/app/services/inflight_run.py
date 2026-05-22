"""Top-level run lifecycle + recovery service (M2.5.2).

Single choke point for everything that mutates :class:`InflightRun`. The
chat WebSocket handler (``app.api.v1.sessions``) and the non-interactive
runner (``app.services.agent_runner``) call into ``register_run`` /
``update_last_seen`` / ``transition`` for every top-level turn; the
FastAPI lifespan hook calls ``recover_inflight_runs`` once at startup;
the 5-minute :func:`app.jobs.inflight_recovery.reap_stale_inflight_runs`
ARQ cron calls ``reap_stale`` for the long-tail "still RUNNING but the
worker is hung" case.

Recovery strategy
-----------------

``pid_token = host:pid:start_seconds`` is unique per process across
restarts (PID can be re-used after a process exits, but the start time
will differ). Two flavours of recovery share the same primitive:

1. **Startup sweep** (``recover_inflight_runs``). Reads every RUNNING
   row whose ``pid_token`` does NOT equal the current process's token
   and flips it to ``LOST``. Cheap SELECT + UPDATE — runs once at
   container boot.
2. **Cron sweep** (``reap_stale``). Reads every RUNNING row whose
   ``last_seen_at`` is more than 15 min behind. For each, double-check
   whether the original PID is still alive on this host (``os.kill(pid,
   0)``) before flipping — same-host hung worker stays RUNNING (the
   cron runs again in 5 min); a worker on another host is conservatively
   marked LOST.

The notification fan-out emits ``inflight_run.lost_detected`` with
``target_audience='actor'`` and ``cooldown_seconds=0`` so the user who
fired the run sees it in their bell within seconds of the recovery
sweep landing.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.agent import Agent
from app.db.models.identity import Identity
from app.db.models.inflight_run import (
    ERROR_KIND_MAX_CHARS,
    PID_TOKEN_MAX_CHARS,
    InflightRun,
    InflightRunState,
)
from app.db.models.session import Session as SessionModel
from app.repositories.inflight_run import InflightRunRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_FORCE_RECYCLED",
    "AUDIT_FORCE_RECYCLE_FAILED",
    "AUDIT_RECOVERED_LOST",
    "AUDIT_REGISTERED",
    "AUDIT_TIMED_OUT_TO_LOST",
    "AUDIT_TRANSITION",
    "AUDIT_TRANSITION_FAILED",
    "CONSOLE_RECENT_TERMINAL_WINDOW_SECONDS",
    "DEFAULT_REAP_BATCH_LIMIT",
    "ERROR_KIND_ADMIN_FORCE_RECYCLE",
    "ERROR_KIND_BACKEND_RESTART",
    "ERROR_KIND_HEARTBEAT_TIMEOUT",
    "EVENT_FORCE_RECYCLED",
    "EVENT_LOST_DETECTED",
    "STALE_LAST_SEEN_SECONDS",
    "ConsoleStateBucket",
    "InflightRunWithMeta",
    "RunNotFoundError",
    "RunTerminalError",
    "RuntimeConsoleStats",
    "console_state_bucket",
    "current_pid_token",
    "force_recycle_run",
    "list_active_for_console",
    "list_lost_for_session",
    "list_stale_running",
    "process_part",
    "reap_stale",
    "recover_inflight_runs",
    "register_run",
    "runtime_console_stats",
    "transition",
    "update_last_seen",
]


# ─── Tunables ───────────────────────────────────────────────
# 15 minutes of silence on a RUNNING row triggers the cron's
# pid-liveness check + LOST flip. Picked deliberately above the
# longest expected run (channel + flow turns top out around 5 min),
# so a slow-but-healthy run isn't reaped.
STALE_LAST_SEEN_SECONDS = 15 * 60

DEFAULT_REAP_BATCH_LIMIT = 200

# Recently-terminal rows still surfaced on the runtime console so the
# admin can see "this got killed 12 minutes ago" without having to dig
# through audit. Anything older than this window is filtered out by the
# console listing query.
CONSOLE_RECENT_TERMINAL_WINDOW_SECONDS = 30 * 60

ERROR_KIND_BACKEND_RESTART = "backend_restart"
ERROR_KIND_HEARTBEAT_TIMEOUT = "heartbeat_timeout"
ERROR_KIND_ADMIN_FORCE_RECYCLE = "admin_force_recycle"

EVENT_LOST_DETECTED = "inflight_run.lost_detected"
EVENT_FORCE_RECYCLED = "inflight_run.force_recycled"

# ─── Audit action keys ──────────────────────────────────────
AUDIT_REGISTERED = "inflight_run.registered"
AUDIT_TRANSITION = "inflight_run.state_transitioned"
AUDIT_RECOVERED_LOST = "inflight_run.recovered_lost"
AUDIT_TIMED_OUT_TO_LOST = "inflight_run.timed_out_to_lost"
AUDIT_TRANSITION_FAILED = "inflight_run.transition_failed"
AUDIT_FORCE_RECYCLED = "inflight_run.force_recycled"
AUDIT_FORCE_RECYCLE_FAILED = "inflight_run.force_recycle_failed"


# ─── Terminal states ────────────────────────────────────────
_TERMINAL_STATES: frozenset[InflightRunState] = frozenset(
    {
        InflightRunState.COMPLETED,
        InflightRunState.LOST,
        InflightRunState.CANCELLED,
        InflightRunState.FAILED,
    }
)


# ─── PID token helpers ──────────────────────────────────────
def current_pid_token() -> str:
    """``host:pid:start_seconds`` — unique per process across restarts.

    The trailing ``start_seconds`` is what survives PID reuse: when the
    OS reassigns a PID to a fresh process the start time changes, so
    two tokens with the same ``host:pid`` prefix but different boot
    epochs unambiguously refer to different processes.
    """
    host = (socket.gethostname() or "unknown")[:40]
    return f"{host}:{os.getpid()}:{int(time.time())}"


def process_part(token: str | None) -> str | None:
    """Strip the trailing start-time slice — used for prefix equality.

    Two tokens share a process when ``host:pid`` matches *and*
    ``start_seconds`` matches. We compare on the full string for safety
    but expose this helper so callers can debug-print the human part.
    """
    if not token:
        return None
    parts = token.split(":")
    if len(parts) < 3:
        return token
    return ":".join(parts[:2])


def _trim_pid_token(token: str | None) -> str | None:
    if token is None:
        return None
    return token[:PID_TOKEN_MAX_CHARS]


def _trim_error_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    return kind[:ERROR_KIND_MAX_CHARS]


def _is_pid_alive_locally(pid: int) -> bool:
    """``os.kill(pid, 0)`` probe with conservative defaults.

    Returns True when:
      - ``os.kill`` succeeds (process exists and we have permission)
      - ``PermissionError`` (process exists, we just can't signal it)

    Returns False for ``ProcessLookupError`` (no such pid) and any
    other OSError. Best-effort across POSIX + Windows; the 15-minute
    ``last_seen_at`` cutoff is the real safety net so a false positive
    here only means the cron runs again in 5 min.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_from_token(token: str | None) -> int | None:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _host_from_token(token: str | None) -> str | None:
    if not token:
        return None
    parts = token.split(":")
    return parts[0] if parts else None


# ─── Snapshot helpers ───────────────────────────────────────
def _coerce_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Strip the legitimately-non-JSON pieces of a ``RunRequest`` dump.

    Drop attachment bytes (kept on the user message row); keep only the
    counts / mime types so a future ``/retry`` can rebuild a fresh
    request without persisting the raw payload twice.
    """
    if not snapshot:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key == "attachments" and isinstance(value, list):
            cleaned["attachments"] = [
                {
                    "kind": str(item.get("kind") or ""),
                    "mime_type": str(item.get("mime_type") or ""),
                    "size_bytes": (
                        len(item["data"])
                        if isinstance(item.get("data"), (bytes, bytearray))
                        else None
                    ),
                }
                for item in value
                if isinstance(item, dict)
            ]
            continue
        cleaned[key] = value
    return cleaned


# ─── Lifecycle ──────────────────────────────────────────────
async def register_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    backend_kind: str,
    request_snapshot: dict[str, Any] | None = None,
    agent_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
    pid_token: str | None = None,
) -> InflightRun:
    """Record the spine row for a freshly started top-level run.

    Idempotent on ``run_id``: if a row already exists (re-attach after
    a transient socket blip) we return the existing row and refresh
    ``last_seen_at`` instead of raising on the unique-index conflict.
    Caller commits.
    """
    repo = InflightRunRepository(db)
    existing = await repo.get_by_run_id(run_id=run_id)
    if existing is not None:
        existing.last_seen_at = utcnow_naive()
        await db.flush([existing])
        return existing

    token = _trim_pid_token(pid_token or current_pid_token())
    now = utcnow_naive()
    row = InflightRun(
        run_id=run_id,
        session_id=session_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        identity_id=identity_id,
        backend_kind=str(backend_kind)[:40],
        request_snapshot=_coerce_snapshot(request_snapshot),
        last_event_seq=0,
        state=InflightRunState.RUNNING,
        pid_token=token,
        started_at=now,
        last_seen_at=now,
    )
    db.add(row)
    await db.flush([row])

    await audit_svc.record(
        db,
        action=AUDIT_REGISTERED,
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="inflight_run",
        resource_id=row.id,
        summary=(f"inflight run registered: run_id={run_id} backend={backend_kind}"),
        metadata={
            "run_id": str(run_id),
            "session_id": str(session_id),
            "agent_id": str(agent_id) if agent_id else None,
            "backend_kind": backend_kind,
            "pid_token": token,
        },
    )

    from app.services.agent_runtime import publish_workspace_summary_for

    await publish_workspace_summary_for(db, workspace_id=workspace_id)
    return row


async def update_last_seen(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    last_event_seq: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Bump ``last_seen_at`` for the matching row.

    Returns True when a row was updated, False when no spine row exists
    or the row has already moved to a terminal state. Cheap UPDATE; no
    audit (these fire on every event). Caller commits.
    """
    repo = InflightRunRepository(db)
    row = await repo.get_by_run_id(run_id=run_id)
    if row is None or row.state in _TERMINAL_STATES:
        return False
    row.last_seen_at = now or utcnow_naive()
    if last_event_seq is not None:
        row.last_event_seq = max(int(last_event_seq), int(row.last_event_seq))
    await db.flush([row])
    return True


async def transition(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    target_state: InflightRunState,
    error_kind: str | None = None,
    finished_at: datetime | None = None,
    reason: str | None = None,
) -> InflightRun | None:
    """Move the row through the state machine + write one audit row.

    Idempotent: same-state writes refresh ``last_seen_at`` only;
    terminal-state rows ignore further transitions. Returns the row
    (or ``None`` when there is no spine row at all). Caller commits.

    On crash we still write a stable ``inflight_run.transition_failed``
    audit so the operator has a breadcrumb without re-raising into the
    chat turn (the user-facing run already failed; we don't want a
    bookkeeping crash to cascade).
    """
    try:
        repo = InflightRunRepository(db)
        row = await repo.get_by_run_id(run_id=run_id)
        if row is None:
            return None

        previous = row.state
        if previous == target_state:
            row.last_seen_at = utcnow_naive()
            await db.flush([row])
            return row
        if previous in _TERMINAL_STATES:
            return row

        row.state = target_state
        row.last_seen_at = utcnow_naive()
        if error_kind is not None:
            row.error_kind = _trim_error_kind(error_kind)
        if target_state in _TERMINAL_STATES:
            row.finished_at = finished_at or utcnow_naive()
        await db.flush([row])

        await audit_svc.record(
            db,
            action=AUDIT_TRANSITION,
            actor_identity_id=row.identity_id,
            workspace_id=row.workspace_id,
            resource_type="inflight_run",
            resource_id=row.id,
            summary=(
                f"inflight run {row.run_id} {previous.value} → "
                f"{target_state.value}" + (f": {reason}" if reason else "")
            ),
            metadata={
                "run_id": str(row.run_id),
                "session_id": str(row.session_id),
                "from_state": previous.value,
                "to_state": target_state.value,
                "error_kind": row.error_kind,
                "reason": reason,
            },
        )

        from app.services.agent_runtime import publish_workspace_summary_for

        await publish_workspace_summary_for(db, workspace_id=row.workspace_id)
        return row
    except Exception as exc:
        log.exception(
            "inflight_run transition failed run_id=%s target=%s",
            run_id,
            target_state.value,
        )
        try:
            await audit_svc.record(
                db,
                action=AUDIT_TRANSITION_FAILED,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="inflight_run",
                resource_id=None,
                summary=(
                    f"inflight transition crashed for run_id={run_id} target={target_state.value}"
                ),
                metadata={
                    "run_id": str(run_id),
                    "target_state": target_state.value,
                    "error_class": type(exc).__name__,
                    "error_repr": repr(exc)[:500],
                },
            )
        except Exception:  # pragma: no cover - audit best-effort
            log.exception("inflight_run.transition_failed audit also crashed")
        return None


# ─── Recovery sweep (startup) ───────────────────────────────
async def recover_inflight_runs(
    db: AsyncSession,
    *,
    current_token: str | None = None,
    emit_notification: bool = True,
) -> dict[str, int]:
    """Flip RUNNING rows owned by previous processes to LOST.

    Called once from the FastAPI lifespan at startup. Caller commits
    — the lifespan hook wraps this in its own ``async with factory()``
    block and commits after.

    Returns ``{"recovered_count", "alive_count", "notified_count"}``
    so the lifespan log line is informative without trawling audit.
    """
    token = current_token or current_pid_token()
    repo = InflightRunRepository(db)
    rows = await repo.list_running(limit=DEFAULT_REAP_BATCH_LIMIT)

    recovered = 0
    alive = 0
    notified = 0
    now = utcnow_naive()
    touched_workspaces: set[uuid.UUID] = set()

    for row in rows:
        if row.pid_token == token:
            alive += 1
            continue
        # We are very deliberately NOT probing PID liveness here: a fresh
        # process on the same host cannot share a start_seconds with the
        # previous incarnation, and a different host is unreachable.
        row.state = InflightRunState.LOST
        row.error_kind = ERROR_KIND_BACKEND_RESTART
        row.finished_at = now
        recovered += 1
        touched_workspaces.add(row.workspace_id)
        await db.flush([row])

        await audit_svc.record(
            db,
            action=AUDIT_RECOVERED_LOST,
            actor_identity_id=None,
            workspace_id=row.workspace_id,
            resource_type="inflight_run",
            resource_id=row.id,
            summary=(
                f"inflight run {row.run_id} marked LOST after backend restart "
                f"(prev_pid_token={row.pid_token!r}, current={token!r})"
            ),
            metadata={
                "run_id": str(row.run_id),
                "session_id": str(row.session_id),
                "prev_pid_token": row.pid_token,
                "current_pid_token": token,
                "trigger": "backend_restart",
            },
        )

        if emit_notification:
            try:
                from app.services.notification_events import emit_event

                await emit_event(
                    db,
                    event_key=EVENT_LOST_DETECTED,
                    workspace_id=row.workspace_id,
                    actor_identity_id=row.identity_id,
                    cooldown_resource_id=str(row.id),
                    payload={
                        "inflight_run_id": str(row.id),
                        "run_id": str(row.run_id),
                        "session_id": str(row.session_id),
                        "agent_id": (str(row.agent_id) if row.agent_id else None),
                        "trigger": "backend_restart",
                        "resource_type": "inflight_run",
                        "resource_id": str(row.id),
                    },
                )
                notified += 1
            except Exception:  # pragma: no cover - best-effort
                log.exception(
                    "inflight_run.lost_detected emit failed for run %s",
                    row.run_id,
                )

    if touched_workspaces:
        from app.services.agent_runtime import publish_workspace_summary_for

        for ws_id in touched_workspaces:
            await publish_workspace_summary_for(db, workspace_id=ws_id)

    return {
        "recovered_count": recovered,
        "alive_count": alive,
        "notified_count": notified,
    }


# ─── Recovery sweep (cron) ──────────────────────────────────
async def list_stale_running(
    db: AsyncSession,
    *,
    stale_after_seconds: int = STALE_LAST_SEEN_SECONDS,
    now: datetime | None = None,
    limit: int = DEFAULT_REAP_BATCH_LIMIT,
) -> list[InflightRun]:
    """Reaper input — RUNNING rows whose ``last_seen_at`` is too old."""
    cutoff = (now or utcnow_naive()) - timedelta(seconds=stale_after_seconds)
    repo = InflightRunRepository(db)
    return list(await repo.list_stale_running(cutoff=cutoff, limit=limit))


async def reap_stale(
    db: AsyncSession,
    *,
    current_token: str | None = None,
    stale_after_seconds: int = STALE_LAST_SEEN_SECONDS,
    now: datetime | None = None,
    limit: int = DEFAULT_REAP_BATCH_LIMIT,
    emit_notification: bool = True,
) -> dict[str, int]:
    """Cron entrypoint — flip stale RUNNING rows to LOST.

    Same contract as :func:`recover_inflight_runs` but scoped to the
    long-tail "still RUNNING but went silent" case. Same-host hung
    workers are deliberately spared via :func:`_is_pid_alive_locally`
    so we don't kill an in-progress run that's just slow.
    """
    token = current_token or current_pid_token()
    current_host = _host_from_token(token)
    stale_rows = await list_stale_running(
        db,
        stale_after_seconds=stale_after_seconds,
        now=now,
        limit=limit,
    )

    reaped = 0
    spared_alive = 0
    notified = 0
    now_dt = now or utcnow_naive()
    touched_workspaces: set[uuid.UUID] = set()

    for row in stale_rows:
        host = _host_from_token(row.pid_token)
        pid = _pid_from_token(row.pid_token)
        if (
            host is not None
            and current_host is not None
            and host == current_host
            and pid is not None
            and _is_pid_alive_locally(pid)
        ):
            spared_alive += 1
            continue

        row.state = InflightRunState.LOST
        row.error_kind = ERROR_KIND_HEARTBEAT_TIMEOUT
        row.finished_at = now_dt
        touched_workspaces.add(row.workspace_id)
        await db.flush([row])

        await audit_svc.record(
            db,
            action=AUDIT_TIMED_OUT_TO_LOST,
            actor_identity_id=None,
            workspace_id=row.workspace_id,
            resource_type="inflight_run",
            resource_id=row.id,
            summary=(
                f"inflight run {row.run_id} marked LOST after silent for "
                f"{(now_dt - row.last_seen_at).total_seconds():.0f}s"
            ),
            metadata={
                "run_id": str(row.run_id),
                "session_id": str(row.session_id),
                "stale_seconds": int(stale_after_seconds),
                "last_seen_at": row.last_seen_at.isoformat(),
                "pid_token": row.pid_token,
                "trigger": "heartbeat_timeout",
            },
        )
        reaped += 1

        if emit_notification:
            try:
                from app.services.notification_events import emit_event

                await emit_event(
                    db,
                    event_key=EVENT_LOST_DETECTED,
                    workspace_id=row.workspace_id,
                    actor_identity_id=row.identity_id,
                    cooldown_resource_id=str(row.id),
                    payload={
                        "inflight_run_id": str(row.id),
                        "run_id": str(row.run_id),
                        "session_id": str(row.session_id),
                        "agent_id": (str(row.agent_id) if row.agent_id else None),
                        "trigger": "heartbeat_timeout",
                        "resource_type": "inflight_run",
                        "resource_id": str(row.id),
                    },
                )
                notified += 1
            except Exception:  # pragma: no cover - best-effort
                log.exception(
                    "inflight_run.lost_detected emit failed for run %s",
                    row.run_id,
                )

    if touched_workspaces:
        from app.services.agent_runtime import publish_workspace_summary_for

        for ws_id in touched_workspaces:
            await publish_workspace_summary_for(db, workspace_id=ws_id)

    return {
        "stale_seen": len(stale_rows),
        "reaped": reaped,
        "spared_alive": spared_alive,
        "notified_count": notified,
    }


# ─── WS reconnect helper ────────────────────────────────────
async def list_lost_for_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int = 20,
) -> list[InflightRun]:
    """Recent ``LOST`` rows for one session.

    The WS reconnect handshake calls this once per accept; if the list
    is non-empty we push a ``system`` frame so the user can decide
    whether to ``/retry`` or move on.
    """
    repo = InflightRunRepository(db)
    return list(
        await repo.list_lost_for_session(
            session_id=session_id,
            workspace_id=workspace_id,
            limit=limit,
        )
    )


# ─── Runtime console (M4.1) ─────────────────────────────────
# The console projects the persisted state machine (``RUNNING /
# PAUSED / COMPLETED / LOST / CANCELLED / FAILED``) onto five
# admin-facing buckets. ``zombie`` and ``killed`` are deliberately NOT
# new enum values — they are derived projections of (state, error_kind)
# so the schema stays untouched (the M2.5.2 row spine is canonical).
ConsoleStateBucket = str  # one of: running / paused / lost / zombie / killed


# Eligible source states for the listing query. Terminal states are
# filtered to the recent window in the listing helper itself; transient
# states (RUNNING / PAUSED) are always included.
_CONSOLE_LISTED_STATES: tuple[InflightRunState, ...] = (
    InflightRunState.RUNNING,
    InflightRunState.PAUSED,
    InflightRunState.LOST,
    InflightRunState.CANCELLED,
)


def console_state_bucket(state: InflightRunState, error_kind: str | None) -> ConsoleStateBucket:
    """Project ``(state, error_kind)`` onto a runtime-console label.

    Mapping rationale:

    * ``RUNNING`` / ``PAUSED`` → same name (live worker, possibly
      blocked on an approval).
    * ``LOST`` with ``error_kind=heartbeat_timeout`` → ``zombie`` (the
      reaper noticed a hang).
    * Other ``LOST`` rows → ``lost`` (e.g. ``backend_restart``).
    * ``CANCELLED`` rows whose ``error_kind`` carries the force-recycle
      tag → ``killed``; otherwise the canonical ``cancelled`` label
      doesn't need its own admin-console bucket and gets folded into
      ``killed`` (terminal user-or-admin cancel).
    * ``COMPLETED`` / ``FAILED`` are filtered upstream — they don't
      appear on the runtime console.
    """
    if state == InflightRunState.RUNNING:
        return "running"
    if state == InflightRunState.PAUSED:
        return "paused"
    if state == InflightRunState.LOST:
        if (error_kind or "").lower() == ERROR_KIND_HEARTBEAT_TIMEOUT:
            return "zombie"
        return "lost"
    if state == InflightRunState.CANCELLED:
        return "killed"
    return state.value


@dataclass(slots=True, frozen=True)
class InflightRunWithMeta:
    """Enriched runtime-console row (DTO returned by ``list_active_for_console``).

    Built once per page render — the joined session / agent / identity
    rows are the only joins required, and the index on
    ``(state, last_seen_at)`` keeps the base scan cheap. ``token_estimate``
    is a best-effort projection; the WebSocket handler does not currently
    persist running-tally usage on the spine row, so the field is None
    unless the request snapshot carried an opening estimate.
    """

    inflight_run_id: uuid.UUID
    run_id: uuid.UUID
    session_id: uuid.UUID
    session_label: str | None
    agent_id: uuid.UUID | None
    agent_name: str | None
    identity_id: uuid.UUID | None
    identity_email: str | None
    state: InflightRunState
    state_bucket: ConsoleStateBucket
    backend_kind: str
    started_at: datetime
    last_seen_at: datetime
    finished_at: datetime | None
    elapsed_seconds: float
    last_event_seq: int
    token_estimate: int | None
    error_kind: str | None
    workspace_id: uuid.UUID


@dataclass(slots=True, frozen=True)
class RuntimeConsoleStats:
    """Counter card payload for the runtime console dashboard."""

    running: int
    paused: int
    lost: int
    zombie: int
    killed: int
    total_active: int


def _request_snapshot_token_estimate(
    snapshot: dict[str, Any] | None,
) -> int | None:
    """Best-effort token estimate from the persisted request snapshot.

    The chat / channel run path stashes a trimmed snapshot of the
    originating request; if a caller ever fills in an opening token
    estimate (M0.13 reserved key ``token_estimate``) we surface it here.
    Otherwise we fall through to ``None`` rather than fabricating a
    number from word counts.
    """
    if not isinstance(snapshot, dict):
        return None
    candidate = snapshot.get("token_estimate")
    if candidate is None:
        candidate = snapshot.get("input_tokens")
    try:
        if candidate is None:
            return None
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _filter_console_states(
    requested: list[ConsoleStateBucket] | None,
) -> set[ConsoleStateBucket] | None:
    """Normalize the API ``?state=`` filter into a bucket whitelist.

    Returns ``None`` when no filter (or a vacuous one) was supplied —
    the listing helper interprets ``None`` as "include every bucket".
    """
    if not requested:
        return None
    allowed = {"running", "paused", "lost", "zombie", "killed"}
    cleaned = {s.strip().lower() for s in requested if isinstance(s, str)}
    cleaned &= allowed
    return cleaned or None


async def list_active_for_console(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    limit: int = 200,
    now: datetime | None = None,
    states: list[ConsoleStateBucket] | None = None,
) -> list[InflightRunWithMeta]:
    """Return runtime-console rows for one workspace.

    Includes:

    * Every transient row (``RUNNING`` / ``PAUSED``) for the workspace.
    * Recently-terminal rows (``LOST`` / ``CANCELLED``) whose
      ``finished_at`` is inside :data:`CONSOLE_RECENT_TERMINAL_WINDOW_SECONDS`
      so the admin can see a kill they just dispatched without
      bouncing to audit.

    Joins sessions / agents / identities so the table can render a
    label, an agent name, and the originating user without a separate
    fan-out.
    """
    moment = now or utcnow_naive()
    cutoff = moment - timedelta(seconds=CONSOLE_RECENT_TERMINAL_WINDOW_SECONDS)
    state_filter = _filter_console_states(states)

    stmt = (
        select(InflightRun, SessionModel.title, Agent.name, Identity.email)
        .join(SessionModel, SessionModel.id == InflightRun.session_id)
        .join(Agent, Agent.id == InflightRun.agent_id, isouter=True)
        .join(
            Identity,
            Identity.id == InflightRun.identity_id,
            isouter=True,
        )
        .where(InflightRun.workspace_id == workspace_id)
        .where(InflightRun.state.in_(_CONSOLE_LISTED_STATES))
        .where(
            (InflightRun.state.in_((InflightRunState.RUNNING, InflightRunState.PAUSED)))
            | (InflightRun.finished_at >= cutoff)
        )
        .order_by(desc(InflightRun.last_seen_at))
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    out: list[InflightRunWithMeta] = []
    for row, session_title, agent_name, identity_email in rows:
        bucket = console_state_bucket(row.state, row.error_kind)
        if state_filter is not None and bucket not in state_filter:
            continue
        elapsed = ((row.finished_at or moment) - row.started_at).total_seconds()
        out.append(
            InflightRunWithMeta(
                inflight_run_id=row.id,
                run_id=row.run_id,
                session_id=row.session_id,
                session_label=session_title,
                agent_id=row.agent_id,
                agent_name=agent_name,
                identity_id=row.identity_id,
                identity_email=identity_email,
                state=row.state,
                state_bucket=bucket,
                backend_kind=row.backend_kind,
                started_at=row.started_at,
                last_seen_at=row.last_seen_at,
                finished_at=row.finished_at,
                elapsed_seconds=max(0.0, float(elapsed)),
                last_event_seq=int(row.last_event_seq or 0),
                token_estimate=_request_snapshot_token_estimate(row.request_snapshot),
                error_kind=row.error_kind,
                workspace_id=row.workspace_id,
            )
        )
    return out


async def runtime_console_stats(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    now: datetime | None = None,
) -> RuntimeConsoleStats:
    """Counter aggregation for the runtime console dashboard cards.

    Computed from the same listing helper so the bucket projection
    stays canonical — there's no separate SQL aggregation to drift
    out of sync. Cheap on workspaces with low concurrency (the listing
    is index-bounded by ``state + last_seen_at``); workspaces in the
    1k+ active runs regime would warrant a dedicated COUNT query, but
    that's beyond M4.1.
    """
    rows = await list_active_for_console(db, workspace_id=workspace_id, now=now, limit=500)
    counters = {
        "running": 0,
        "paused": 0,
        "lost": 0,
        "zombie": 0,
        "killed": 0,
    }
    for row in rows:
        counters[row.state_bucket] = counters.get(row.state_bucket, 0) + 1
    total_active = counters["running"] + counters["paused"]
    return RuntimeConsoleStats(
        running=counters["running"],
        paused=counters["paused"],
        lost=counters["lost"],
        zombie=counters["zombie"],
        killed=counters["killed"],
        total_active=total_active,
    )


# ─── Force recycle (M4.1) ───────────────────────────────────
class RunNotFoundError(Exception):
    """Raised by :func:`force_recycle_run` when no spine row exists.

    Maps to a 404 in the API layer. Carries the run id only so it never
    leaks workspace details when a cross-tenant probe lands on the
    endpoint.
    """

    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"inflight run {run_id} not found")
        self.run_id = run_id


class RunTerminalError(Exception):
    """Raised by :func:`force_recycle_run` when the row already terminal.

    Force-recycling a row that has already settled to LOST / KILLED /
    CANCELLED / COMPLETED / FAILED is a no-op the API surface should
    reject as a 409 — without this the second click would mistakenly
    issue a redundant audit + notification.
    """

    def __init__(self, run_id: uuid.UUID, state: InflightRunState) -> None:
        super().__init__(f"inflight run {run_id} already terminal ({state.value})")
        self.run_id = run_id
        self.state = state


async def force_recycle_run(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    request: Any | None = None,
) -> dict[str, Any]:
    """Admin-driven cancel of a live inflight run.

    Sequence:

    1. Look up the spine row by ``run_id`` and reject when it belongs
       to a different workspace (cross-tenant safety).
    2. Issue a best-effort ``backend.cancel(run_id)`` — the kernel
       cancels the asyncio task; we do **not** wait for the task to
       wind down because the row transition is the source of truth
       for the UI state, and the backend's ``finally`` block writes
       its own COMPLETED / FAILED transition shortly after.
    3. Transition the row to ``CANCELLED`` with
       ``error_kind=admin_force_recycle``. This is idempotent through
       :func:`transition` — a same-state retry is harmless.
    4. Audit ``inflight_run.force_recycled``.
    5. Emit an in-app notification to the actor so the admin sees a
       confirmation in the bell.

    Raises:

    * :class:`RunNotFoundError` — no spine row, or row belongs to a
      different workspace.
    * :class:`RunTerminalError` — row already settled. Caller maps
      to 409.
    """
    repo = InflightRunRepository(db)
    row = await repo.get_by_run_id(run_id=run_id)
    if row is None or row.workspace_id != workspace_id:
        raise RunNotFoundError(run_id)
    if row.state in _TERMINAL_STATES:
        raise RunTerminalError(run_id, row.state)

    cancel_ok = True
    cancel_error: str | None = None
    try:
        from app.agents.kernels.registry import get_backend

        backend = get_backend(row.backend_kind)
        if backend is not None:
            await backend.cancel(run_id)
        else:
            cancel_ok = False
            cancel_error = f"unknown backend_kind={row.backend_kind!r}"
    except Exception as exc:  # pragma: no cover - defensive
        cancel_ok = False
        cancel_error = type(exc).__name__
        log.warning(
            "force_recycle_run: backend.cancel failed run=%s kind=%s",
            run_id,
            row.backend_kind,
            exc_info=True,
        )

    transitioned = await transition(
        db,
        run_id=run_id,
        target_state=InflightRunState.CANCELLED,
        error_kind=ERROR_KIND_ADMIN_FORCE_RECYCLE,
        reason="admin_force_recycle",
    )
    killed_at = (transitioned.finished_at if transitioned else None) or utcnow_naive()

    await audit_svc.record(
        db,
        action=AUDIT_FORCE_RECYCLED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="inflight_run",
        resource_id=row.id,
        summary=(f"admin force-recycled inflight run {run_id} ({row.backend_kind})"),
        metadata={
            "run_id": str(run_id),
            "session_id": str(row.session_id),
            "agent_id": str(row.agent_id) if row.agent_id else None,
            "backend_kind": row.backend_kind,
            "previous_state": row.state.value,
            "cancel_dispatched": cancel_ok,
            "cancel_error": cancel_error,
        },
        request=request,
    )

    if not cancel_ok:
        # Distinct audit row so the operator can spot adapters whose
        # cancel hook silently failed without scrolling the success
        # row's metadata. Forces the dashboard to flag the failure
        # without breaking the user-visible "killed" toast — the row
        # state already moved to CANCELLED, so the run is gone either
        # way.
        await audit_svc.record(
            db,
            action=AUDIT_FORCE_RECYCLE_FAILED,
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="inflight_run",
            resource_id=row.id,
            summary=(f"backend.cancel did not execute for run {run_id}"),
            metadata={
                "run_id": str(run_id),
                "backend_kind": row.backend_kind,
                "cancel_error": cancel_error,
            },
            request=request,
        )

    try:
        from app.services.notification_events import emit_event

        await emit_event(
            db,
            event_key=EVENT_FORCE_RECYCLED,
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            cooldown_resource_id=str(row.id),
            payload={
                "inflight_run_id": str(row.id),
                "run_id": str(run_id),
                "session_id": str(row.session_id),
                "agent_id": str(row.agent_id) if row.agent_id else None,
                "trigger": "admin_force_recycle",
                "resource_type": "inflight_run",
                "resource_id": str(row.id),
            },
            request=request,
        )
    except Exception:  # pragma: no cover - best-effort
        log.exception("inflight_run.force_recycled emit failed for run %s", run_id)

    return {
        "run_id": str(run_id),
        "inflight_run_id": str(row.id),
        "state": InflightRunState.CANCELLED.value,
        "previous_state": row.state.value,
        "killed_at": killed_at.isoformat(),
        "cancel_dispatched": cancel_ok,
        "cancel_error": cancel_error,
    }
