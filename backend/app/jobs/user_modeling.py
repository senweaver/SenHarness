"""Daily user-modeling extraction sweep ARQ task (M3.7).

Schedule
--------

Runs once daily at **05:30 UTC**. Slot picked to avoid every existing
neighbour:

* M0.11 retention purge — 04:00 UTC
* M2.3 evolver sweep — 04:30 UTC
* M2.5.2 checkpoint GC — 04:45 UTC
* M3.4 agent profile sweep — 05:00 UTC
* **User-modeling extraction sweep** — **05:30 UTC**

The 30-min gap after M3.4 lets the aux-LLM connection pool settle
between the two daily extractor passes; the M3.4 pass focuses on the
agent side of the dialectic and this one focuses on the user side, so
running them back-to-back would double-load the same provider lane.

Job behaviour (per workspace)
-----------------------------

For each non-deleted workspace, walks identities with at least one
``SessionArtifact`` in the last 30 days and calls
:func:`extract_facts_from_runs` with the M3.7 default
``since_run_count=10``. A failure on one identity is logged + counted;
the sweep keeps going. ARQ ``max_tries=3`` plus the ``on_job_end``
hook below promote a permanent worker crash to
``job.failed_permanent`` once retries exhaust.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services import user_profile as user_profile_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_SWEEP_FAILED_PERMANENT",
    "AUDIT_UPDATE_FAILED",
    "USER_MODELING_SWEEP_NAME",
    "extract_user_facts_sweep",
    "on_user_modeling_job_failed_permanent",
]


USER_MODELING_SWEEP_NAME = "extract_user_facts_sweep"
AUDIT_UPDATE_FAILED = "user_profile.update_failed"
AUDIT_SWEEP_FAILED_PERMANENT = "user_profile.sweep_failed_permanent"


async def extract_user_facts_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily 05:30 UTC. Per workspace + identity (active in 30 days)."""
    _ = ctx
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "status": "ok",
        "workspaces_seen": 0,
        "identities_updated": 0,
        "identities_failed": 0,
        "facts_created": 0,
        "facts_superseded": 0,
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
        identity_ids = await _list_identities(workspace_id=ws_id)
        for identity_id in identity_ids:
            outcome = await _run_one(workspace_id=ws_id, identity_id=identity_id)
            if outcome.get("status") == "ok":
                summary["identities_updated"] += 1
                summary["facts_created"] += int(outcome.get("facts_created", 0))
                summary["facts_superseded"] += int(outcome.get("facts_superseded", 0))
            else:
                summary["identities_failed"] += 1
                if len(summary["errors"]) < 20:
                    summary["errors"].append(outcome)

    return summary


async def _list_identities(*, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    factory = get_session_factory()
    async with factory() as db:
        return await user_profile_svc.list_active_identities(
            db,
            workspace_id=workspace_id,
            since_days=user_profile_svc.DEFAULT_RECENT_DAYS,
        )


async def _run_one(*, workspace_id: uuid.UUID, identity_id: uuid.UUID) -> dict[str, Any]:
    """Drive one ``(workspace, identity)`` extract inside a fresh session."""
    factory = get_session_factory()
    try:
        async with factory() as db:
            outcome = await user_profile_svc.extract_facts_from_runs(
                db,
                workspace_id=workspace_id,
                identity_id=identity_id,
                since_run_count=user_profile_svc.DEFAULT_SINCE_RUN_COUNT,
                invocation_kind="scheduled",
            )
            await db.commit()
        return {
            "status": "ok",
            "workspace_id": str(workspace_id),
            "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
            "facts_created": int(outcome.facts_created),
            "facts_superseded": int(outcome.facts_superseded),
            "facts_unchanged": int(outcome.facts_unchanged),
            "artifacts_examined": int(outcome.artifacts_examined),
            "duration_ms": int(outcome.duration_ms),
            "aux_skipped": bool(outcome.aux_skipped),
            "aux_skip_reason": outcome.aux_skip_reason,
        }
    except Exception as exc:
        log.exception(
            "extract_user_facts_sweep failed for ws=%s identity=%s",
            workspace_id,
            identity_id,
        )
        await _audit_per_identity_failure(
            workspace_id=workspace_id, identity_id=identity_id, exc=exc
        )
        return {
            "status": "failed",
            "workspace_id": str(workspace_id),
            "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _audit_per_identity_failure(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
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
                resource_type="user_profile",
                resource_id=None,
                summary="user_profile extraction failed (per-identity isolation)",
                metadata={
                    "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit is best-effort
        log.exception(
            "extract_user_facts_sweep audit_fallback failed ws=%s identity=%s",
            workspace_id,
            identity_id,
        )


async def on_user_modeling_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """ARQ hook for ``extract_user_facts_sweep`` exhausting its retries."""
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
                summary=(f"extract_user_facts_sweep failed permanently: {exc!r}"),
                metadata={
                    "function": str(ctx.get("function") or USER_MODELING_SWEEP_NAME),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_user_modeling_job_failed_permanent hook crashed")
