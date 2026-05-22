"""ARQ worker settings.

Add real functions to ``WorkerSettings.functions`` as they come online.

Cron jobs registered:

* ``retention_sweep_cascade`` — every 5 minutes, GDPR cascade soft-delete
  for newly soft-deleted identities / workspaces (M0.11).
* ``retention_physical_purge`` — daily at 04:00 UTC, hard-delete rows past
  retention (M0.11). Defaults to dry-run; admin flips
  ``system_settings.retention.physical_purge_enabled`` to ``True`` to
  actually delete.
* ``judge_periodic_sweep`` — hourly run-quality judge backstop (M0.3).
* ``pending_memory_workspace_sweep`` — every 30 minutes (offset by 2 min
  from the retention sweep so the two crons don't fight for connection-
  pool slots), drains M0.7 PENDING memory rows for sessions whose
  synchronous post-FINAL hook never fired.
* ``rollup_skill_usage`` — daily at 02:30 UTC, walks every workspace
  and refreshes ``SkillPack.last_used_at`` + ``effectiveness_avg``
  from the previous 30 days of ``skill_usage`` rows (M1.3).
* ``curator_tick`` — daily at 03:15 UTC, walks every workspace and
  proposes archiving stale skill packs through the approvals
  pipeline (M1.4). 03:15 sits between the M1.3 02:30 rollup and the
  M0.10 03:30 cleanup window.
* ``evolver_workspace_sweep`` — daily at 04:30 UTC, fans the M2.3
  evolver workflow across every non-deleted workspace whose evolver
  is enabled. Sits 30 minutes after the retention purge so the aux
  LLM connection pool isn't competing with GDPR-cascade work.
* ``verify_proposed_versions_sweep`` — every 30 minutes (minute 7
  + 37), validates each ``state=PROPOSED`` SkillPackVersion via
  judge replay (M2.4). Slot picked to avoid the M0.7 minute=2,32
  pending memory sweep, the M0.11 5-minute retention cadence, and
  the M0.3 minute=15 judge backstop.
* ``process_expired_approvals`` — hourly at minute 22 (M2.5).
  Pre-expiry reminder pass for pending approvals within 24h of
  ``expires_at``, then expired processor that auto-archives
  ``skill_pack_archive`` proposals and rejects every other
  expired approval per the roadmap TTL strategy table.
* ``reap_zombies`` — once per minute at second 0 (M2.5.1).
  Sweeps ``subagent_runs`` whose ``last_heartbeat_at`` is older than
  5 minutes, transitions them to ``ZOMBIE``, refunds the parent's
  retry budget, cancels any dangling hallucination Approval, and
  emits the M0.10 ``subagent.zombie_detected`` notification.
* ``reap_stale_inflight_runs`` — every 5 minutes at minute 4/14/24/...
  (M2.5.2). Sweeps ``inflight_runs`` whose ``last_seen_at`` is more
  than 15 minutes behind, transitions them to ``LOST``, and emits
  the ``inflight_run.lost_detected`` notification. Offset 1 minute
  off the M0.11 retention sweep so the two crons don't fight for
  connection-pool slots.
* ``gc_old_checkpoints`` — daily at 04:45 UTC (M2.5.2). Strips
  ``snapshot_json`` bytes on ``session_checkpoints`` rows older than
  30 days while keeping ``parent_checkpoint_id`` lineage + label /
  description so the rewind UI can still reason about historical
  forks. Sits 15 minutes after the M0.11 retention purge so the two
  daily heavy passes don't compete for IO.
* ``hub_auto_pull_sweep`` — every 30 minutes at minute ``{6, 36}``
  (M3.3). For every workspace with at least one auto-pull
  subscription, drafts a PROPOSED local SkillPackVersion when the
  hub publishes a newer version. Slot picked to avoid the M2.4
  verifier (minute={7, 37}), M0.7 pending-memory ({2, 32}), M0.3
  judge backstop (15), and M2.5 approval-TTL (22). Per-subscription
  3-strike retry budget + ``hub.auto_pull_failed_permanent`` audit
  keeps one stuck row from blocking the rest of the sweep.

The M4.6 ``job_runs`` middleware (``on_job_start`` /
``job_run_middleware_end`` chained behind the legacy ``on_job_end``)
records every task lifecycle into the persistent ``job_runs`` table so
the admin Background Jobs page can outlive ARQ's short Redis TTL.
Failure on the metadata side is logged + swallowed so an outage on
the observability spine never breaks the actual cron work.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any, ClassVar

from arq import cron

from app.core.config import settings
from app.jobs.agent_profile_update import (
    on_agent_profile_job_failed_permanent,
    update_agent_profiles_sweep,
)
from app.jobs.approval_ttl import (
    on_approval_ttl_job_failed_permanent,
    process_expired_approvals,
)
from app.jobs.curator import (
    curator_apply_approved,
    curator_propose_archive,
    curator_tick,
)
from app.jobs.evolver import (
    evolver_workspace_sweep,
    on_evolver_job_failed_permanent,
)
from app.jobs.hub_auto_pull import (
    hub_auto_pull_sweep,
    on_hub_auto_pull_job_failed_permanent,
)
from app.jobs.inflight_recovery import (
    gc_old_checkpoints,
    on_inflight_recovery_job_failed_permanent,
    reap_stale_inflight_runs,
)
from app.jobs.insights import (
    generate_insights,
    on_insights_job_failed_permanent,
)
from app.jobs.judge import (
    judge_periodic_sweep,
    judge_session_artifact,
    on_job_failed_permanent,
    score_message_alignment,
)
from app.jobs.pending_memory import (
    on_pending_memory_job_failed_permanent,
    pending_memory_workspace_sweep,
)
from app.jobs.retention import (
    on_retention_job_failed_permanent,
    retention_physical_purge,
    retention_sweep_cascade,
)
from app.jobs.skill_telemetry import rollup_skill_usage
from app.jobs.skill_verify import (
    on_skill_verify_job_failed_permanent,
    verify_proposed_versions_sweep,
)
from app.jobs.subagent_zombie import (
    on_subagent_zombie_job_failed_permanent,
    reap_zombies,
)
from app.jobs.user_modeling import (
    extract_user_facts_sweep,
    on_user_modeling_job_failed_permanent,
)
from app.worker.arq_middleware import (
    job_run_middleware_end,
    job_run_middleware_start,
)

log = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    ctx["started"] = True
    log.info("arq worker starting; registered funcs=%d", len(WorkerSettings.functions))


async def shutdown(ctx: dict) -> None:
    log.info("arq worker stopping")


async def heartbeat(ctx: dict[str, Any]) -> str:
    """No-op ping used so arq has at least one registered function.

    Can be enqueued as ``await redis.enqueue_job("heartbeat")`` for liveness
    checks; the result is a simple ISO timestamp string.
    """
    await asyncio.sleep(0)
    from datetime import datetime

    return datetime.now(UTC).isoformat()


_RETENTION_TASKS: frozenset[str] = frozenset(
    {
        retention_sweep_cascade.__name__,
        retention_physical_purge.__name__,
    }
)
_PENDING_MEMORY_TASKS: frozenset[str] = frozenset({pending_memory_workspace_sweep.__name__})
_EVOLVER_TASKS: frozenset[str] = frozenset({evolver_workspace_sweep.__name__})
_SKILL_VERIFY_TASKS: frozenset[str] = frozenset({verify_proposed_versions_sweep.__name__})
_APPROVAL_TTL_TASKS: frozenset[str] = frozenset({process_expired_approvals.__name__})
_SUBAGENT_ZOMBIE_TASKS: frozenset[str] = frozenset({reap_zombies.__name__})
_INFLIGHT_RECOVERY_TASKS: frozenset[str] = frozenset(
    {
        reap_stale_inflight_runs.__name__,
        gc_old_checkpoints.__name__,
    }
)
_AGENT_PROFILE_TASKS: frozenset[str] = frozenset({update_agent_profiles_sweep.__name__})
_USER_MODELING_TASKS: frozenset[str] = frozenset({extract_user_facts_sweep.__name__})
_HUB_AUTO_PULL_TASKS: frozenset[str] = frozenset({hub_auto_pull_sweep.__name__})
_INSIGHTS_TASKS: frozenset[str] = frozenset({generate_insights.__name__})


async def _legacy_failure_dispatch(ctx: dict[str, Any]) -> None:
    """Original ``on_job_end`` body: route permanent failures to per-task hooks.

    Split out of :func:`on_job_end` so the M4.6 metadata middleware can
    chain *behind* the legacy dispatcher in a single declared
    ``on_job_end`` callable. The behaviour is unchanged: each task
    family that registered a permanent-failure handler still fires
    its own audit / notification path on the third strike.
    """
    job_try = int(ctx.get("job_try", 0) or 0)
    max_tries = int(ctx.get("max_tries", WorkerSettings.max_tries) or 0)
    exc = ctx.get("exception")
    if exc is None or job_try < max_tries:
        return
    function_name = str(ctx.get("function") or "")
    if function_name in {
        score_message_alignment.__name__,
        judge_session_artifact.__name__,
    }:
        await on_job_failed_permanent(ctx, exc)
    elif function_name in _RETENTION_TASKS:
        await on_retention_job_failed_permanent(ctx, exc)
    elif function_name in _PENDING_MEMORY_TASKS:
        await on_pending_memory_job_failed_permanent(ctx, exc)
    elif function_name in _EVOLVER_TASKS:
        await on_evolver_job_failed_permanent(ctx, exc)
    elif function_name in _SKILL_VERIFY_TASKS:
        await on_skill_verify_job_failed_permanent(ctx, exc)
    elif function_name in _APPROVAL_TTL_TASKS:
        await on_approval_ttl_job_failed_permanent(ctx, exc)
    elif function_name in _SUBAGENT_ZOMBIE_TASKS:
        await on_subagent_zombie_job_failed_permanent(ctx, exc)
    elif function_name in _INFLIGHT_RECOVERY_TASKS:
        await on_inflight_recovery_job_failed_permanent(ctx, exc)
    elif function_name in _AGENT_PROFILE_TASKS:
        await on_agent_profile_job_failed_permanent(ctx, exc)
    elif function_name in _USER_MODELING_TASKS:
        await on_user_modeling_job_failed_permanent(ctx, exc)
    elif function_name in _HUB_AUTO_PULL_TASKS:
        await on_hub_auto_pull_job_failed_permanent(ctx, exc)
    elif function_name in _INSIGHTS_TASKS:
        await on_insights_job_failed_permanent(ctx, exc)


async def on_job_end(ctx: dict[str, Any]) -> None:
    """ARQ end-of-job hook chain.

    Order matters:

    1. The legacy permanent-failure dispatcher fires first so the
       per-task audit / notification path keeps its existing
       semantics — the M4.6 metadata write is independent and must
       not preempt the routing decision.
    2. The M4.6 ``job_run_middleware_end`` records the terminal
       status (success / failed / failed_permanent) into the
       ``job_runs`` table. Wrapped in try/except defence-in-depth
       (the middleware itself already swallows DB errors); a
       metadata failure must never block the per-task hook.
    """
    try:
        await _legacy_failure_dispatch(ctx)
    finally:
        try:
            await job_run_middleware_end(ctx)
        except Exception as exc:  # pragma: no cover - svc swallows
            log.warning("job_run.on_job_end_chain_failed err=%s", exc)


async def on_job_start(ctx: dict[str, Any]) -> None:
    """ARQ start-of-job hook — flip ``job_runs`` row to ``RUNNING``.

    Best-effort: any error inside the middleware is logged and
    swallowed so a metadata hiccup never blocks an actual task from
    running. The middleware also stamps ``ctx["start_ms"]`` so
    :func:`on_job_end` can compute ``duration_ms`` even if the task
    body never tracked its own timing.
    """
    try:
        await job_run_middleware_start(ctx)
    except Exception as exc:  # pragma: no cover - svc swallows
        log.warning("job_run.on_job_start_failed err=%s", exc)


class WorkerSettings:
    redis_settings = None  # Filled at runtime from settings.redis_url
    on_startup = startup
    on_shutdown = shutdown
    on_job_start = on_job_start
    on_job_end = on_job_end
    functions: ClassVar[list] = [
        heartbeat,
        score_message_alignment,
        judge_session_artifact,
        judge_periodic_sweep,
        retention_sweep_cascade,
        retention_physical_purge,
        pending_memory_workspace_sweep,
        rollup_skill_usage,
        curator_tick,
        curator_propose_archive,
        curator_apply_approved,
        evolver_workspace_sweep,
        verify_proposed_versions_sweep,
        process_expired_approvals,
        reap_zombies,
        reap_stale_inflight_runs,
        gc_old_checkpoints,
        update_agent_profiles_sweep,
        extract_user_facts_sweep,
        hub_auto_pull_sweep,
        generate_insights,
    ]
    cron_jobs: ClassVar[list] = [
        # Every 5 minutes; matches the docs/extensions-and-governance.md SLA
        # (Retention cascade section).
        cron(
            retention_sweep_cascade,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        # Daily 04:00 UTC — outside the 03:00 GC window so the two don't fight
        # for connection-pool slots.
        cron(retention_physical_purge, hour={4}, minute={0}),
        # Hourly run-quality judge backstop — minute 15 keeps it clear
        # of the on-the-hour retention sweep.
        cron(judge_periodic_sweep, minute={15}, run_at_startup=False),
        # M0.7 — pending memory backstop sweep. Offset (minute 2 + 32) to
        # avoid the 5-minute retention sweep cadence and the minute-15
        # judge slot, so all three crons spread out over the hour.
        cron(
            pending_memory_workspace_sweep,
            minute={2, 32},
            run_at_startup=False,
        ),
        # M1.3 — daily skill usage rollup. 02:30 UTC sits in the dead
        # zone between the M0.11 retention 5-minute cadence and the
        # 03:30 cleanup window so connection pools don't contend.
        cron(rollup_skill_usage, hour={2}, minute={30}, run_at_startup=False),
        # M1.4 — daily Skill Curator sweep. 03:15 UTC clears the M1.3
        # rollup at 02:30 and the M0.10 cleanup at 03:30; the only
        # neighbour is the on-the-hour 5-min retention sweep at 03:15
        # itself which scopes to disjoint tables.
        cron(curator_tick, hour={3}, minute={15}, run_at_startup=False),
        # M2.3 — daily evolver workflow sweep. 04:30 UTC sits 30 min
        # after the M0.11 retention purge at 04:00 so the aux-LLM
        # connection pool isn't competing with GDPR-cascade hard
        # deletes; no other slot exists at minute={30} of the hour
        # other than the 5-min retention sweep which scopes to
        # disjoint tables.
        cron(evolver_workspace_sweep, hour={4}, minute={30}, run_at_startup=False),
        # M2.4 — every 30 minutes, verify PROPOSED SkillPackVersion rows
        # via judge replay. Slot picked to avoid clashing with the M0.7
        # pending-memory sweep (minute 2 + 32), the M0.11 5-minute
        # retention cadence (minute 0/5/10/...), and the M0.3 minute=15
        # judge backstop. Minutes 7 + 37 sit in the dead zones between
        # those slots and never overlap the daily evolver / curator /
        # rollup runs.
        cron(
            verify_proposed_versions_sweep,
            minute={7, 37},
            run_at_startup=False,
        ),
        # M2.5 — hourly Approval TTL processor. Slot 22 chosen to
        # avoid every existing cron neighbour: M0.7 minute={2,32} /
        # M0.11 5-min cadence (0/5/10/.../55) / M0.3 minute=15 /
        # M2.4 minute={7,37} / daily slots at 02:30 / 03:15 / 03:30
        # / 04:00 / 04:30. Minute 22 sits inside the 20-25 5-min
        # retention bracket but the retention sweep scopes to
        # disjoint tables (identities / workspaces) so they don't
        # contend for connection-pool slots.
        cron(
            process_expired_approvals,
            minute={22},
            run_at_startup=False,
        ),
        # M2.5.1 — sub-agent zombie reaper. ARQ's ``cron`` accepts a
        # ``second`` kwarg natively; ``second={0}`` makes the sweep
        # fire once per minute at second 0, which is the 60-second
        # cadence the M2.5.1 design demands. Heartbeat threshold is
        # 5 minutes so a single missed tick is harmless. Cheap query
        # (index-only seek on ``ix_subagent_runs_state_heartbeat``)
        # so the every-minute slot doesn't add measurable load.
        cron(
            reap_zombies,
            second={0},
            run_at_startup=False,
        ),
        # M2.5.2 — top-level inflight run reaper. Every 5 minutes,
        # offset 4 minutes off the M0.11 retention sweep (which fires
        # at 0/5/10/...) and 3 minutes off the M0.7 pending memory
        # sweep (minute 2/32) so all three sweeps spread out and
        # don't compete for connection-pool slots. 15-min stale
        # threshold sits well above the longest expected interactive
        # turn so a slow run isn't reaped mid-flight.
        cron(
            reap_stale_inflight_runs,
            minute={4, 14, 24, 34, 44, 54},
            run_at_startup=False,
        ),
        # M2.5.2 — daily checkpoint snapshot GC. 04:45 UTC sits 15 min
        # after the M2.3 evolver sweep (04:30) and 45 min after the
        # M0.11 physical purge (04:00) so the heavy daily passes
        # don't pile up. Strips ``snapshot_json`` bytes on rows older
        # than 30 days while keeping the lineage chain intact.
        cron(
            gc_old_checkpoints,
            hour={4},
            minute={45},
            run_at_startup=False,
        ),
        # M3.4 — daily agent_profile sweep. 05:00 UTC sits 15 min
        # after the M2.5.2 checkpoint GC (04:45) and 30 min after
        # the M2.3 evolver sweep (04:30) so the heavy daily passes
        # don't pile up. Skips per-agent on aux breaker open; one
        # bad agent never tanks the workspace via per-agent
        # try/except + AUDIT_UPDATE_FAILED.
        cron(
            update_agent_profiles_sweep,
            hour={5},
            minute={0},
            run_at_startup=False,
        ),
        # M3.7 — daily user-modeling extraction sweep. 05:30 UTC sits
        # 30 min after the M3.4 agent profile pass so the aux-LLM
        # connection lane settles between the agent-side and user-
        # side dialectic extractors. Skips per-identity on aux
        # breaker open; one bad identity never tanks the workspace
        # via per-identity try/except + AUDIT_UPDATE_FAILED.
        cron(
            extract_user_facts_sweep,
            hour={5},
            minute={30},
            run_at_startup=False,
        ),
        # M3.3 — hub auto-pull sweep every 30 minutes at minute={6,36}.
        # Slot picked to avoid every existing cron neighbour:
        # M0.11 retention 5-min cadence (0/5/10/.../55), M0.7 pending
        # memory (minute={2,32}), M0.3 judge backstop (minute=15),
        # M2.4 verifier sweep (minute={7,37}), M2.5 approval-TTL
        # (minute=22). Minutes {6,36} are the only 30-min cadence
        # that doesn't touch any neighbour. Per-subscription
        # try/except + 3-strike AUDIT_AUTO_PULL_FAILED_PERMANENT
        # keeps one stuck workspace from blocking the rest.
        cron(
            hub_auto_pull_sweep,
            minute={6, 36},
            run_at_startup=False,
        ),
    ]
    max_jobs: int = 16
    job_timeout: int = 300
    # Three-strike rule: each ARQ task gets up to 3 attempts before the
    # ``on_job_end`` hook above promotes the failure to ``job.failed_permanent``.
    max_tries: int = 3

    @classmethod
    def _inject_redis(cls) -> None:
        from arq.connections import RedisSettings

        cls.redis_settings = RedisSettings.from_dsn(settings.redis_url)


WorkerSettings._inject_redis()
