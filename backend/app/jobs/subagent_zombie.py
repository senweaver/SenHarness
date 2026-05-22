"""Sub-agent zombie reaper (M2.5.1) — 60-second ARQ cron.

Backstop for the heartbeat lifecycle hook in
:mod:`app.agents.harness.subagents`. Every 60 seconds we sweep
``subagent_runs`` for rows whose ``state='running'`` and
``last_heartbeat_at`` is older than five minutes
(:data:`HEARTBEAT_DEAD_SECONDS`) and:

1. Transition the row to ``ZOMBIE`` via
   :func:`app.services.subagent_run.reap_zombie` (writes the
   ``subagent.zombie_reaped`` audit + emits the M0.10
   ``subagent.zombie_detected`` notification).
2. Best-effort cancel any dangling Approval row (most often the
   hallucination review) so admins don't see a phantom card after
   the run is closed.
3. Refund the parent's retry budget when applicable so the parent
   can launch one more attempt before the cap kicks in.

Cron slot is ``second={0}`` so it fires once per minute at second 0;
ARQ's `cron(...)` accepts the second-level kwarg natively.

3-strike permanent failure routes through
``on_subagent_zombie_job_failed_permanent`` so an operator gets one
``job.failed_permanent`` notification before the watermark advances.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from app.core.security import utcnow_naive
from app.db.models.approval import ApprovalStatus
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.db.session import get_session_factory
from app.repositories.approval import ApprovalRepository
from app.services import audit as audit_svc
from app.services import subagent_run as subagent_svc

log = logging.getLogger(__name__)

__all__ = [
    "REAP_ZOMBIES_NAME",
    "on_subagent_zombie_job_failed_permanent",
    "reap_zombies",
]


REAP_ZOMBIES_NAME = "reap_zombies"

# Defensive upper bound per tick so a runaway workspace can't lock
# the worker for the whole minute. Five minutes of dead heartbeats
# rarely produces more than a handful of stale rows on a healthy
# tenant, so 200 is a comfortable headroom.
_REAP_BATCH_LIMIT = 200


# ─── Cron entrypoint ─────────────────────────────────────────
async def reap_zombies(ctx: dict[str, Any]) -> dict[str, Any]:
    """One sweep across stale ``subagent_runs`` rows.

    Returns a JSON-friendly summary so admin diagnostics can compare
    two ticks without trawling logs. Per-row failures are isolated so
    one bad workspace cannot starve the rest of the queue.
    """
    _ = ctx
    summary: dict[str, Any] = {
        "status": "ok",
        "stale_seen": 0,
        "reaped": 0,
        "approvals_cancelled": 0,
        "budget_refunded": 0,
        "errored": 0,
    }

    now = utcnow_naive()
    factory = get_session_factory()

    async with factory() as db:
        stale_rows = await subagent_svc.list_stale(
            db,
            heartbeat_dead_seconds=subagent_svc.HEARTBEAT_DEAD_SECONDS,
            now=now,
            limit=_REAP_BATCH_LIMIT,
        )
        summary["stale_seen"] = len(stale_rows)
        # Snapshot enough state to drive the per-row work without
        # holding a long-lived transaction; each reap commits on its
        # own session so a failure on row N doesn't roll back N-1.
        snapshots = [_snapshot(row) for row in stale_rows]

    for snap in snapshots:
        try:
            row = await _reap_one(snap=snap, now=now, summary=summary)
            if row is not None:
                summary["reaped"] += 1
        except Exception:
            log.exception("reap_zombies: failure on subagent_run %s", snap.spine_id)
            summary["errored"] += 1

    return summary


# ─── Per-row work ────────────────────────────────────────────
class _StaleSnapshot:
    """Tiny holder so we don't keep ORM rows across sessions."""

    __slots__ = (
        "child_run_id",
        "hallucination_approval_id",
        "last_heartbeat_at",
        "parent_run_id",
        "retry_budget",
        "retry_count",
        "spawn_depth",
        "spine_id",
        "workspace_id",
    )

    def __init__(self, row: SubAgentRun) -> None:
        self.spine_id = row.id
        self.workspace_id = row.workspace_id
        self.child_run_id = row.child_run_id
        self.parent_run_id = row.parent_run_id
        self.spawn_depth = row.spawn_depth
        self.retry_count = row.retry_count
        self.retry_budget = row.retry_budget
        self.last_heartbeat_at = row.last_heartbeat_at
        self.hallucination_approval_id = row.hallucination_approval_id


def _snapshot(row: SubAgentRun) -> _StaleSnapshot:
    return _StaleSnapshot(row)


async def _reap_one(
    *,
    snap: _StaleSnapshot,
    now: datetime,
    summary: dict[str, Any],
) -> SubAgentRun | None:
    """Single-row reap: transition + approval cleanup + budget refund."""
    factory = get_session_factory()

    async with factory() as db:
        # Re-fetch under the new session so a parallel reaper run can't
        # double-process the same row — the second worker sees state ≠
        # RUNNING and short-circuits.
        from app.repositories.subagent_run import (
            SubAgentRunRepository,
        )

        repo = SubAgentRunRepository(db)
        live = await repo.get(snap.spine_id)
        if live is None or live.state != SubAgentRunState.RUNNING:
            return None
        # Heartbeat may have caught up between snapshot + reap.
        if (now - live.last_heartbeat_at).total_seconds() < (subagent_svc.HEARTBEAT_DEAD_SECONDS):
            return None

        reason = (
            f"no heartbeat for {(now - live.last_heartbeat_at).total_seconds():.0f}s "
            f"(dead threshold {subagent_svc.HEARTBEAT_DEAD_SECONDS}s)"
        )
        reaped = await subagent_svc.reap_zombie(
            db,
            child_run_id=live.child_run_id,
            reason=reason,
        )
        await _cancel_dangling_approval(
            db,
            workspace_id=reaped.workspace_id,
            approval_id=reaped.hallucination_approval_id,
            summary=summary,
        )
        # Refund retry budget — parent gets one extra shot at relaunching.
        if reaped.retry_count > 0:
            reaped.retry_count = max(0, reaped.retry_count - 1)
            await db.flush([reaped])
            summary["budget_refunded"] += 1
            await audit_svc.record(
                db,
                action=subagent_svc.AUDIT_HEARTBEAT_LOST,
                actor_identity_id=None,
                workspace_id=reaped.workspace_id,
                resource_type="subagent_run",
                resource_id=reaped.id,
                summary=(
                    f"refunded retry budget for {reaped.child_run_id} "
                    f"({reaped.retry_count}/{reaped.retry_budget})"
                ),
                metadata={
                    "child_run_id": str(reaped.child_run_id),
                    "parent_run_id": str(reaped.parent_run_id),
                    "retry_count": int(reaped.retry_count),
                    "retry_budget": int(reaped.retry_budget),
                    "reason": "zombie_reaped",
                },
            )
        await db.commit()
        return reaped


async def _cancel_dangling_approval(
    db: Any,
    *,
    workspace_id: uuid.UUID,
    approval_id: uuid.UUID | None,
    summary: dict[str, Any],
) -> None:
    """Best-effort cancel of a parked hallucination Approval row."""
    if approval_id is None:
        return
    repo = ApprovalRepository(db)
    decided = await repo.decide(
        approval_id=approval_id,
        workspace_id=workspace_id,
        approved=False,
        reason="parent run zombified",
        decided_by_identity_id=None,
        now=utcnow_naive(),
        status_override=ApprovalStatus.CANCELLED,
    )
    if decided is not None and decided.status == ApprovalStatus.CANCELLED:
        summary["approvals_cancelled"] += 1


# ─── ARQ permanent-failure hook ──────────────────────────────
async def on_subagent_zombie_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """Three-strike hook for ``reap_zombies``.

    Mirrors the pending-memory / curator / approval-TTL hooks: writes
    one stable audit row so operators can spot the dead-letter sweep
    without trawling Redis. Best-effort; never re-raises.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=f"reap_zombies failed permanently: {exc!r}",
                metadata={
                    "function": str(ctx.get("function") or REAP_ZOMBIES_NAME),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc)[:500],
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("on_subagent_zombie_job_failed_permanent crashed")
