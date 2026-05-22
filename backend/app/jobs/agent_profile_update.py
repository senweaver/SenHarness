"""Daily agent_profile update sweep ARQ task (M3.4).

Schedule
--------

Runs once daily at **05:00 UTC**. Slot picked to avoid every existing
neighbour:

* M0.11 retention purge — 04:00 UTC
* M2.3 evolver sweep — 04:30 UTC
* M2.5.2 checkpoint GC — 04:45 UTC
* **Agent profile sweep** — **05:00 UTC**
* Then a long quiet stretch until the on-the-hour 5-min retention sweep.

05:00 sits 15 minutes after the daily checkpoint GC so the connection-
pool footprint of the heavy 04:30 / 04:45 daily passes has settled
back before the per-agent aux-LLM clustering fans out.

Job behaviour (per workspace)
-----------------------------

For each non-deleted workspace, walk every non-deleted agent and call
:func:`update_profile_for_agent` with ``since_days=30``. A failure on
one agent is logged and counted; the sweep continues.

Failure isolation
-----------------

A per-agent exception is caught and audited under
``agent_profile.update_failed``; the sweep keeps going. ARQ's
``max_tries=3`` retry budget plus the ``on_job_end`` hook below
write one ``job.failed_permanent`` audit row when the outer driver
itself crashes three runs in a row.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.services import agent_profile as agent_profile_svc
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AGENT_PROFILE_SWEEP_NAME",
    "AUDIT_SWEEP_FAILED_PERMANENT",
    "AUDIT_UPDATE_FAILED",
    "on_agent_profile_job_failed_permanent",
    "update_agent_profiles_sweep",
]


AGENT_PROFILE_SWEEP_NAME = "update_agent_profiles_sweep"
AUDIT_SWEEP_FAILED_PERMANENT = "agent_profile.sweep_failed_permanent"
AUDIT_UPDATE_FAILED = "agent_profile.update_failed"


async def update_agent_profiles_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron tick across every non-deleted workspace + agent."""
    _ = ctx
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "status": "ok",
        "workspaces_seen": 0,
        "agents_updated": 0,
        "agents_failed": 0,
        "errors": [],
    }

    async with factory() as db:
        workspace_ids = list(
            (await db.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None))))
            .scalars()
            .all()
        )

    for ws_id in workspace_ids:
        summary["workspaces_seen"] += 1
        agent_ids = await _list_agents(workspace_id=ws_id)
        for agent_id in agent_ids:
            outcome = await _run_one(workspace_id=ws_id, agent_id=agent_id)
            if outcome.get("status") == "ok":
                summary["agents_updated"] += 1
            else:
                summary["agents_failed"] += 1
                if len(summary["errors"]) < 20:
                    summary["errors"].append(outcome)

    return summary


async def _list_agents(*, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(Agent.id).where(
                Agent.workspace_id == workspace_id,
                Agent.deleted_at.is_(None),
            )
        )
        return list(rows.scalars().all())


async def _run_one(*, workspace_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any]:
    """Update one (workspace, agent) pair inside a fresh session."""
    factory = get_session_factory()
    try:
        async with factory() as db:
            outcome = await agent_profile_svc.update_profile_for_agent(
                db,
                workspace_id=workspace_id,
                agent_id=agent_id,
                since_days=agent_profile_svc.DEFAULT_SINCE_DAYS,
                invocation_kind="scheduled",
            )
            await db.commit()
        return {
            "status": "ok",
            "workspace_id": str(workspace_id),
            "agent_id": str(agent_id),
            "aggregated_run_count": int(outcome.aggregated_run_count),
            "duration_ms": int(outcome.duration_ms),
        }
    except Exception as exc:
        log.exception(
            "update_agent_profiles_sweep failed for ws=%s agent=%s",
            workspace_id,
            agent_id,
        )
        await _audit_per_agent_failure(workspace_id=workspace_id, agent_id=agent_id, exc=exc)
        return {
            "status": "failed",
            "workspace_id": str(workspace_id),
            "agent_id": str(agent_id),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _audit_per_agent_failure(
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    exc: BaseException,
) -> None:
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_UPDATE_FAILED,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="agent_profile",
                resource_id=agent_id,
                summary="agent_profile update failed (per-agent isolation)",
                metadata={
                    "agent_id": str(agent_id),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit is best-effort
        log.exception(
            "update_agent_profiles_sweep audit_fallback failed ws=%s agent=%s",
            workspace_id,
            agent_id,
        )


async def on_agent_profile_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """ARQ hook for ``update_agent_profiles_sweep`` exhausting its retries."""
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_SWEEP_FAILED_PERMANENT,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(f"update_agent_profiles_sweep failed permanently: {exc!r}"),
                metadata={
                    "function": str(ctx.get("function") or AGENT_PROFILE_SWEEP_NAME),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_agent_profile_job_failed_permanent hook crashed")
