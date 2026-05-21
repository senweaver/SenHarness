"""Daily evolver workspace sweep ARQ task (M2.3).

Schedule
--------

Runs once daily at **04:30 UTC**. The slot is wedged between the
existing nightly schedule (no contender within ±10 minutes):

* M0.11 retention sweep — every 5 min on the hour
* M0.7 pending memory sweep — minute {2, 32}
* M0.3 judge periodic sweep — minute {15}
* M1.3 skill usage rollup — 02:30 UTC
* M1.4 skill curator — 03:15 UTC
* M0.10 cleanup — 03:30 UTC
* M0.11 retention purge — 04:00 UTC
* **Evolver workflow sweep** — **04:30 UTC**

04:30 sits 30 minutes after the retention purge so connection-pool
usage settles back before the workflow's per-workspace aux-LLM calls
fan out.

Job behaviour (per workspace)
-----------------------------

For each non-deleted workspace:

1. Resolve :class:`~app.schemas.platform_settings.EvolverSettings`.
2. Skip the workspace entirely when ``enabled is False`` (the dispatcher
   audits ``evolver.workflow_skipped`` with reason ``evolver_disabled``).
3. Skip when the shared evolver breaker is open (audit
   ``evolver.workflow_skipped`` with reason ``breaker_open``).
4. Otherwise call :func:`evolve_workspace_skills` with
   ``invocation_kind="scheduled"``. The dispatcher routes to the
   workflow / agent engine per the workspace's pinned choice and
   returns a :class:`WorkflowExecutionResult` regardless of outcome
   (skip / success / internal exception).

Failure isolation
-----------------

A per-workspace exception is caught + logged + counted; the sweep
continues to the next workspace. Only an outer crash (e.g.
``get_session_factory`` itself raises) bubbles to ARQ so the
worker's ``max_tries=3`` retry budget protects against transient
infra blips.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services.evolver_workflow import (
    AUDIT_FAILED,
    WorkflowExecutionResult,
    evolve_workspace_skills,
)

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_WORKFLOW_FAILED_PERMANENT",
    "EVOLVER_SWEEP_NAME",
    "evolver_workspace_sweep",
    "on_evolver_job_failed_permanent",
]


EVOLVER_SWEEP_NAME = "evolver_workspace_sweep"
AUDIT_WORKFLOW_FAILED_PERMANENT = "evolver.workflow_failed_permanent"


# ─── Cron entrypoint ─────────────────────────────────────────
async def evolver_workspace_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily evolver sweep across every non-deleted workspace.

    Returns a JSON-serialisable summary mirroring the M1.4 curator
    sweep shape so operator dashboards can compare ticks side by
    side without bespoke parsers.
    """
    _ = ctx
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "status": "ok",
        "workspaces_seen": 0,
        "workspaces_skipped": 0,
        "workspaces_failed": 0,
        "proposals_created": 0,
        "results": [],
    }

    async with factory() as db:
        ws_rows = (
            (
                await db.execute(
                    select(Workspace.id).where(Workspace.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )

    for ws_id in ws_rows:
        summary["workspaces_seen"] += 1
        result = await _run_for_workspace(workspace_id=ws_id)
        summary["results"].append(result.to_dict() if isinstance(result, WorkflowExecutionResult) else result)
        if isinstance(result, WorkflowExecutionResult):
            if result.skipped:
                summary["workspaces_skipped"] += 1
            elif result.error is not None:
                summary["workspaces_failed"] += 1
            summary["proposals_created"] += int(result.proposals_created)
        else:
            summary["workspaces_failed"] += 1

    return summary


async def _run_for_workspace(
    *, workspace_id: uuid.UUID
) -> WorkflowExecutionResult | dict[str, Any]:
    """Run the dispatcher for a single workspace inside a fresh session.

    Wrapped in a broad try/except so one workspace's failure cannot
    poison the rest of the sweep — the audit + log capture the
    diagnostic, the loop continues.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            return await evolve_workspace_skills(
                db,
                workspace_id=workspace_id,
                invocation_kind="scheduled",
                actor_identity_id=None,
                bypass_min_artifacts=False,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "evolver_workspace_sweep failed for workspace=%s", workspace_id
        )
        try:
            async with factory() as db:
                await audit_svc.record(
                    db,
                    action=AUDIT_FAILED,
                    actor_identity_id=None,
                    workspace_id=workspace_id,
                    resource_type="workspace",
                    resource_id=workspace_id,
                    summary="evolver workspace sweep raised before completion",
                    metadata={
                        "engine": "dispatcher",
                        "invocation_kind": "scheduled",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception(
                "evolver_workspace_sweep audit_fallback failed ws=%s", workspace_id
            )
        return {
            "workspace_id": str(workspace_id),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


# ─── ARQ permanent-failure hook ─────────────────────────────
async def on_evolver_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """ARQ hook for ``evolver_workspace_sweep`` exhausting its retries.

    Mirrors the curator / pending-memory hooks: writes one stable
    audit row so operators can spot the dead-letter sweep without
    trawling Redis. Best-effort; never re-raises.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_WORKFLOW_FAILED_PERMANENT,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(
                    f"evolver_workspace_sweep failed permanently: {exc!r}"
                ),
                metadata={
                    "function": str(ctx.get("function") or EVOLVER_SWEEP_NAME),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_evolver_job_failed_permanent hook crashed")
