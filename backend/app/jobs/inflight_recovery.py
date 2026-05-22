"""Top-level run recovery + checkpoint GC ARQ tasks (M2.5.2).

Two crons share this module:

* :func:`reap_stale_inflight_runs` — every 5 minutes (offset 1 minute
  off the M0.11 retention sweep so the two don't fight for connection
  slots). Catches RUNNING ``inflight_runs`` rows whose ``last_seen_at``
  has fallen 15+ minutes behind even within a still-running process.
  Same recovery primitive as the FastAPI lifespan startup hook (mark
  LOST + audit + emit ``inflight_run.lost_detected``).

* :func:`gc_old_checkpoints` — daily at 04:45 UTC. Empties
  ``snapshot_json`` on ``session_checkpoints`` rows older than 30 days
  while keeping the row, ``parent_checkpoint_id`` lineage, and
  metadata. Matches the M2.5.2 design ("retain lineage, prune bytes").

Both jobs follow the M0.x permanent-failure convention: the
``on_inflight_recovery_job_failed_permanent`` hook routes 3-strike
failures into a stable ``job.failed_permanent`` audit so an operator
gets one breadcrumb without re-raising into the queue.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.security import utcnow_naive
from app.db.models.checkpoint import SessionCheckpoint
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services import inflight_run as inflight_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_CHECKPOINT_GC_PRUNED",
    "CHECKPOINT_GC_AGE_DAYS",
    "GC_OLD_CHECKPOINTS_NAME",
    "REAP_STALE_INFLIGHT_RUNS_NAME",
    "gc_old_checkpoints",
    "on_inflight_recovery_job_failed_permanent",
    "reap_stale_inflight_runs",
]


REAP_STALE_INFLIGHT_RUNS_NAME = "reap_stale_inflight_runs"
GC_OLD_CHECKPOINTS_NAME = "gc_old_checkpoints"

# 30 days matches the operational rule of thumb in
# ``docs/runtime-and-jobs.md`` (Inflight runs section) — long enough
# that a forked branch stays explorable for a release cycle, short
# enough that the JSONB bloat stays bounded on a chatty workspace.
CHECKPOINT_GC_AGE_DAYS = 30

AUDIT_CHECKPOINT_GC_PRUNED = "checkpoint.gc_pruned"


# ─── Cron entrypoint: stale inflight runs ─────────────────────
async def reap_stale_inflight_runs(ctx: dict[str, Any]) -> dict[str, Any]:
    """One sweep across stale ``inflight_runs`` rows.

    Returns a JSON-friendly summary so operator diagnostics can compare
    two ticks without trawling logs. Per-row failures are isolated by
    the underlying service so one bad workspace cannot starve the
    queue.
    """
    _ = ctx
    factory = get_session_factory()
    async with factory() as db:
        result = await inflight_svc.reap_stale(db)
        await db.commit()

    log.info("reap_stale_inflight_runs: %s", result)
    return {"status": "ok", **result}


# ─── Cron entrypoint: checkpoint GC ───────────────────────────
async def gc_old_checkpoints(ctx: dict[str, Any]) -> dict[str, Any]:
    """Strip ``snapshot_json`` bytes on aged ``session_checkpoints``.

    We keep the row, ``parent_checkpoint_id`` lineage, label, and
    description so the rewind / replay UI can still reason about a
    historical fork. Only the heavy ``snapshot_json`` payload is
    cleared — a future "expand" UI can fall back to recomputing from
    the persisted message rows if the user really wants to revisit.
    """
    _ = ctx
    cutoff = utcnow_naive() - timedelta(days=CHECKPOINT_GC_AGE_DAYS)
    factory = get_session_factory()
    pruned = 0
    async with factory() as db:
        rows_to_prune = (
            await db.execute(
                select(SessionCheckpoint.id, SessionCheckpoint.workspace_id)
                .where(SessionCheckpoint.pruned_at.is_(None))
                .where(SessionCheckpoint.created_at < cutoff)
                .limit(1000)
            )
        ).all()
        if not rows_to_prune:
            return {"status": "ok", "pruned": 0}

        ids = [row.id for row in rows_to_prune]
        await db.execute(
            update(SessionCheckpoint)
            .where(SessionCheckpoint.id.in_(ids))
            .values(snapshot_json={}, pruned_at=utcnow_naive())
        )
        pruned = len(ids)

        await audit_svc.record(
            db,
            action=AUDIT_CHECKPOINT_GC_PRUNED,
            actor_identity_id=None,
            workspace_id=None,
            resource_type="session_checkpoint",
            resource_id=None,
            summary=(f"pruned {pruned} session_checkpoints older than {CHECKPOINT_GC_AGE_DAYS}d"),
            metadata={
                "pruned_count": pruned,
                "cutoff": cutoff.isoformat(),
                "age_days": CHECKPOINT_GC_AGE_DAYS,
            },
        )
        await db.commit()

    log.info("gc_old_checkpoints: pruned=%d", pruned)
    return {"status": "ok", "pruned": pruned}


# ─── ARQ permanent-failure hook ───────────────────────────────
async def on_inflight_recovery_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """Three-strike hook for both M2.5.2 cron tasks.

    Mirrors the pending-memory / curator / approval-TTL hooks: writes
    one stable ``job.failed_permanent`` audit row so operators can
    spot the dead-letter sweep without trawling Redis. Best-effort;
    never re-raises.
    """
    factory = get_session_factory()
    function_name = str(ctx.get("function") or "")
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=f"{function_name} failed permanently: {exc!r}",
                metadata={
                    "function": function_name,
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc)[:500],
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("on_inflight_recovery_job_failed_permanent crashed")
