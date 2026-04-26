"""APScheduler-backed Flow scheduler.

Loads cron-triggered Flows from the DB on startup and registers an
``AsyncIOScheduler`` job per flow. Periodically refreshes (every 60s) so new
flows or edits show up without restart.

Runs inside a dedicated container (``scheduler`` service in docker-compose).
When multiple instances are accidentally launched (or during a rolling
deploy where old and new briefly coexist), a Redis lease elects exactly
one leader — see :mod:`app.workflows.leader`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.rate_limit import get_redis
from app.db.models.flow import FlowTriggerKind
from app.db.session import get_session_factory
from app.repositories.flow import FlowRepository
from app.services.flow import trigger_flow
from app.services.gc import run_full_sweep
from app.workflows.leader import run_as_leader

log = logging.getLogger(__name__)

REFRESH_INTERVAL_SEC = 60
GC_JOB_ID = "gc:nightly"
SCHEDULER_LEASE_KEY = "senharness:scheduler:leader"


async def run_forever() -> None:
    """Entry point for the scheduler container — blocks forever.

    Elects a leader via Redis; only the leader actually registers and
    fires cron jobs. Losers poll until the lease becomes available.
    """
    redis_client = get_redis()
    await run_as_leader(
        redis_client,
        lease_key=SCHEDULER_LEASE_KEY,
        on_elected=_run_scheduler_loop,
    )


async def _run_scheduler_loop() -> None:
    """The actual scheduler work — runs only when we hold the lease."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start()
    log.info("APScheduler started; refresh interval=%ds", REFRESH_INTERVAL_SEC)

    # Nightly GC at 03:00 UTC. coalesce + max_instances=1 so we never run two
    # sweeps simultaneously even if the previous one ran long.
    scheduler.add_job(
        _gc_fire,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id=GC_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60 * 60,  # 1h slack for downtime
    )
    log.info("registered nightly GC job at 03:00 UTC")

    known: dict[str, str] = {}  # flow_id -> cron expr (for diffing)

    try:
        while True:
            try:
                await _refresh(scheduler, known)
            except Exception:  # pragma: no cover
                log.exception("scheduler refresh failed")
            await asyncio.sleep(REFRESH_INTERVAL_SEC)
    finally:
        scheduler.shutdown(wait=False)


async def _refresh(scheduler: AsyncIOScheduler, known: dict[str, str]) -> None:
    """Diff the set of cron flows and (un)register jobs accordingly."""
    factory = get_session_factory()
    async with factory() as db:
        flows = await FlowRepository(db).list_enabled_cron_flows()

    current: dict[str, tuple[str, str]] = {}
    for f in flows:
        cfg = f.trigger_config or {}
        expr = str(cfg.get("expr") or "").strip()
        if not expr:
            continue
        tz = str(cfg.get("tz") or "UTC")
        current[str(f.id)] = (expr, tz)

    # Remove jobs that no longer exist or changed expressions.
    for fid in list(known.keys()):
        if fid not in current or known[fid] != current[fid][0]:
            job_id = f"flow:{fid}"
            if scheduler.get_job(job_id) is not None:
                scheduler.remove_job(job_id)
            del known[fid]

    # Add new / updated jobs.
    for fid, (expr, tz) in current.items():
        if fid in known:
            continue
        try:
            trig = CronTrigger.from_crontab(expr, timezone=tz)
        except Exception as e:
            log.warning("bad cron expr for flow %s: %s (%s)", fid, expr, e)
            continue
        scheduler.add_job(
            _fire,
            trigger=trig,
            id=f"flow:{fid}",
            args=[fid],
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
        )
        known[fid] = expr
        log.info("registered cron flow %s expr=%r tz=%s", fid, expr, tz)


async def _gc_fire() -> None:
    """APScheduler callback for the nightly GC."""
    try:
        await run_full_sweep(dry_run=False)
    except Exception:  # pragma: no cover
        log.exception("nightly GC sweep failed")


async def _fire(flow_id_str: str) -> None:
    """APScheduler callback — look the flow up and trigger it."""
    import uuid as _uuid

    flow_id = _uuid.UUID(flow_id_str)
    factory = get_session_factory()
    async with factory() as db:
        flow = await FlowRepository(db).get(flow_id)
        if flow is None:
            return
        ws_id = flow.workspace_id
    try:
        await trigger_flow(
            flow_id,
            workspace_id=ws_id,
            trigger_kind=FlowTriggerKind.CRON,
            payload={"fired_at": datetime.utcnow().isoformat()},
        )
    except Exception:  # pragma: no cover
        log.exception("cron flow fire failed: %s", flow_id)
