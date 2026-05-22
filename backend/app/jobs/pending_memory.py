"""Cache-aware mutation backstop sweep (M0.7).

The synchronous promote hook in :mod:`app.api.v1.sessions` and the
mirror in :mod:`app.services.agent_runner` drain the per-session
``PENDING`` queue immediately after the FINAL frame. This cron is the
backstop for runs that ended without firing the hook — most often a
backend crash mid-turn or a websocket abrupt close.

Runs every 30 minutes. Visits each workspace that has at least one
non-deleted ``PENDING`` row and:

1. Skips rows whose parent session is still recently active so the
   synchronous hook keeps ownership of fresh sessions.
2. Promotes the rest via the same
   :func:`promote_pending_memories_workspace_sweep` path the
   admin debug endpoint uses, so the audit semantics line up exactly.

Like every M0.x ARQ task, three terminal failures route through
``on_job_failed_permanent`` (registered in
:mod:`app.worker.arq_app`) so an operator gets an audit trail before
the watermark advances.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services import pending_memory as pending_memory_svc

log = logging.getLogger(__name__)


_DEFAULT_MAX_AGE_SECONDS = 1800
_WORKSPACE_FANOUT_LIMIT = 200


async def pending_memory_workspace_sweep(
    ctx: dict, max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS
) -> dict[str, Any]:
    """Cron tick — backstop the synchronous promote hook.

    Returns aggregated counts ``{workspaces_visited, promoted, skipped,
    failed}`` so the admin debug endpoint can surface "what just
    happened" without an extra DB read.
    """
    factory = get_session_factory()
    visited = 0
    total_promoted = 0
    total_skipped = 0
    total_failed = 0

    async with factory() as db:
        workspace_ids = await pending_memory_svc.list_active_workspace_ids(
            db, limit=_WORKSPACE_FANOUT_LIMIT
        )

    for workspace_id in workspace_ids:
        try:
            counts = await _sweep_one_workspace(
                workspace_id=workspace_id, max_age_seconds=max_age_seconds
            )
        except Exception as exc:
            log.exception("pending memory sweep failed for workspace %s", workspace_id)
            await _audit_workspace_failure(workspace_id, exc=exc)
            continue
        visited += 1
        total_promoted += counts["promoted"]
        total_skipped += counts["skipped"]
        total_failed += counts["failed"]

    return {
        "workspaces_visited": visited,
        "promoted": total_promoted,
        "skipped": total_skipped,
        "failed": total_failed,
    }


async def _sweep_one_workspace(*, workspace_id: uuid.UUID, max_age_seconds: int) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as db:
        counts = await pending_memory_svc.promote_pending_memories_workspace_sweep(
            db,
            workspace_id=workspace_id,
            max_age_seconds=max_age_seconds,
        )
        if counts["promoted"] or counts["failed"] or counts["skipped"]:
            await audit_svc.record(
                db,
                action="memory.promotion_completed",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="workspace",
                resource_id=workspace_id,
                summary=(
                    f"sweep promoted={counts['promoted']} "
                    f"skipped={counts['skipped']} "
                    f"failed={counts['failed']}"
                ),
                metadata={"trigger": "workspace_sweep", **counts},
            )
        await db.commit()
        return counts


async def _audit_workspace_failure(workspace_id: uuid.UUID, *, exc: BaseException) -> None:
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="memory.promotion_failed",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="workspace",
                resource_id=workspace_id,
                summary="workspace sweep raised — will retry next tick",
                metadata={
                    "error_class": type(exc).__name__,
                    "trigger": "workspace_sweep",
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("audit write for sweep failure failed (ws=%s)", workspace_id)


async def on_pending_memory_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """Three-strike permanent-failure recorder for the sweep cron.

    Workspace-level audit only — the sweep returns aggregated counts,
    so a permanent failure is interpreted as "the orchestration itself
    crashed" rather than "row N of workspace W broke". The per-row
    breadcrumb is already in place via :func:`_sweep_one_workspace`.
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
                summary="pending_memory_workspace_sweep failed permanently",
                metadata={
                    "function": str(ctx.get("function") or ""),
                    "error_class": type(exc).__name__,
                    "job_try": int(ctx.get("job_try", 0) or 0),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("audit write for sweep permanent failure failed")
