"""GDPR retention sweep + physical purge cron jobs (M0.11).

Two ARQ tasks:

* :func:`retention_sweep_cascade` runs every 5 minutes. Reads the
  current watermark for ``identity`` and ``workspace`` scopes, finds
  the next batch of soft-deleted rows, cascades the deletion through
  every applicable :data:`CASCADE_TARGETS` table, and advances the
  watermark on success. Per-row failures get retried up to three times
  in-process before being logged to ``audit_events`` as
  ``job.failed_permanent`` and skipped (so the head of the queue
  never blocks the rest of the batch).

* :func:`retention_physical_purge` runs daily at 04:00 UTC. When
  ``RetentionSettings.physical_purge_enabled`` is ``False`` (default)
  it merely audits a "would purge N rows" report; when ``True`` it
  issues the actual ``DELETE`` per table.

  The M4.6 ``job_runs`` table follows a *per-row* policy
  (success → 60 day TTL, failure → kept indefinitely) instead of the
  whole-table cutoff every other cascade target uses, so the cron
  bolts on a follow-up call to
  :func:`app.services.job_run.purge_expired_success_rows` after the
  CASCADE_TARGETS loop and merges the result into the same audit
  payload + return dict.

Both tasks return JSON-serialisable summary dicts so the admin UI can
render the latest run without a separate read path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.retention_watermark import (
    RetentionScopeKind,
    RetentionWatermark,
)
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services import retention as retention_svc

log = logging.getLogger(__name__)


_CASCADE_RETRY_LIMIT = 3
_BACKOFF_SECONDS: tuple[float, ...] = (0.0, 0.5, 1.5)

# M4.6 — per-row retention for ``job_runs``. Success rows expire 60
# days after the underlying job finished; failure / failed_permanent
# rows are kept indefinitely so post-mortem still works after the
# next quarterly purge. See :mod:`app.services.job_run`.
_JOB_RUNS_SUCCESS_TTL = timedelta(days=60)


# ── Watermark accessors ───────────────────────────────────────
async def _ensure_watermark(db, scope: RetentionScopeKind) -> RetentionWatermark:
    row = (
        await db.execute(
            select(RetentionWatermark).where(
                RetentionWatermark.scope_kind == scope
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = RetentionWatermark(
        scope_kind=scope,
        last_seen_deleted_at=utcnow_naive(),
        last_run_rows_affected=0,
    )
    db.add(row)
    await db.flush()
    return row


# ── Sweep tick ────────────────────────────────────────────────
async def retention_sweep_cascade(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron tick: cascade newly soft-deleted identities + workspaces."""
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "identities_swept": 0,
        "workspaces_swept": 0,
        "rows_cascaded_total": 0,
        "permanent_failures": 0,
    }

    async with factory() as db:
        settings = await retention_svc.get_retention_settings(db)
        batch_size = max(10, int(settings.sweep_batch_size))

    # Identity scope.
    summary.update(
        await _sweep_scope(
            scope=RetentionScopeKind.IDENTITY,
            batch_size=batch_size,
        )
    )
    # Workspace scope (merge counters).
    ws_summary = await _sweep_scope(
        scope=RetentionScopeKind.WORKSPACE,
        batch_size=batch_size,
    )
    summary["workspaces_swept"] = ws_summary["workspaces_swept"]
    summary["rows_cascaded_total"] += ws_summary["rows_cascaded_total"]
    summary["permanent_failures"] += ws_summary["permanent_failures"]

    return summary


async def _sweep_scope(
    *, scope: RetentionScopeKind, batch_size: int
) -> dict[str, Any]:
    """Fetch the next batch for one scope and cascade row-by-row.

    Each row is its own transaction-style commit so a single failure
    can't roll back successful cascades earlier in the batch. The
    watermark advances only after a successful (or permanently-failed +
    audited) cascade.
    """
    factory = get_session_factory()
    swept = 0
    rows_total = 0
    permanent_failures = 0

    async with factory() as db:
        wm = await _ensure_watermark(db, scope)
        cursor: datetime = wm.last_seen_deleted_at
        await db.commit()

    while True:
        async with factory() as db:
            if scope == RetentionScopeKind.IDENTITY:
                pending = await retention_svc.select_pending_identities(
                    db, after=cursor, limit=batch_size
                )
            else:
                pending = await retention_svc.select_pending_workspaces(
                    db, after=cursor, limit=batch_size
                )
        if not pending:
            break

        for scope_id, deleted_at in pending:
            ok, rows = await _cascade_one_with_retry(
                scope=scope, scope_id=scope_id
            )
            if ok:
                swept += 1
                rows_total += rows
            else:
                permanent_failures += 1
            cursor = deleted_at
            await _advance_watermark(
                scope=scope,
                cursor=cursor,
                last_processed_id=scope_id,
                rows_affected=rows,
                error="job.failed_permanent" if not ok else None,
            )

        if len(pending) < batch_size:
            break

    return {
        ("identities_swept" if scope == RetentionScopeKind.IDENTITY else "workspaces_swept"): swept,
        "rows_cascaded_total": rows_total,
        "permanent_failures": permanent_failures,
    }


async def _cascade_one_with_retry(
    *,
    scope: RetentionScopeKind,
    scope_id: uuid.UUID,
) -> tuple[bool, int]:
    """Run one cascade with up to three attempts + exponential backoff.

    Returns ``(success, total_rows_affected)``. On terminal failure we
    audit ``job.failed_permanent`` and return ``(False, 0)`` so the
    caller can advance the watermark past the bad row.
    """
    factory = get_session_factory()
    last_exc: BaseException | None = None
    for attempt in range(_CASCADE_RETRY_LIMIT):
        if attempt:
            await asyncio.sleep(_BACKOFF_SECONDS[attempt])
        try:
            async with factory() as db:
                if scope == RetentionScopeKind.IDENTITY:
                    affected = await retention_svc.cascade_for_identity(
                        db, identity_id=scope_id
                    )
                else:
                    affected = await retention_svc.cascade_for_workspace(
                        db, workspace_id=scope_id
                    )
                total = sum(affected.values())
                await audit_svc.record(
                    db,
                    action="data.cascade_soft_delete",
                    actor_identity_id=None,
                    workspace_id=(
                        scope_id
                        if scope == RetentionScopeKind.WORKSPACE
                        else None
                    ),
                    resource_type=("identity" if scope == RetentionScopeKind.IDENTITY else "workspace"),
                    resource_id=None,
                    summary=(
                        f"cascade {scope.value} {retention_svc.scope_id_hash(scope_id)} "
                        f"affected {total} rows across {len(affected)} tables"
                    ),
                    metadata={
                        "scope_kind": scope.value,
                        "scope_id_hash": retention_svc.scope_id_hash(scope_id),
                        "tables": affected,
                    },
                )
                await db.commit()
                return True, total
        except Exception as exc:
            last_exc = exc
            log.warning(
                "retention cascade attempt %d failed for %s %s: %s",
                attempt + 1,
                scope.value,
                scope_id,
                exc,
            )
    # Three strikes — audit and skip past so we don't head-of-line block.
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=(
                    scope_id if scope == RetentionScopeKind.WORKSPACE else None
                ),
                resource_type="retention_sweep_cascade",
                resource_id=None,
                summary=(
                    f"cascade {scope.value} {retention_svc.scope_id_hash(scope_id)} "
                    f"failed after {_CASCADE_RETRY_LIMIT} attempts"
                ),
                metadata={
                    "task": "retention_sweep_cascade",
                    "scope_kind": scope.value,
                    "scope_id_hash": retention_svc.scope_id_hash(scope_id),
                    "exception": repr(last_exc),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit must not raise
        log.exception("permanent-failure audit write failed")
    return False, 0


async def _advance_watermark(
    *,
    scope: RetentionScopeKind,
    cursor: datetime,
    last_processed_id: uuid.UUID,
    rows_affected: int,
    error: str | None,
) -> None:
    """Persist the new watermark position. Best-effort.

    The error-string column gets the stable code (``job.failed_permanent``)
    when the cascade exhausted its retries; on success we clear the
    column so a transient blip doesn't leave a stale red dot in the
    admin UI.
    """
    factory = get_session_factory()
    async with factory() as db:
        wm = await _ensure_watermark(db, scope)
        wm.last_seen_deleted_at = cursor
        wm.last_processed_id = last_processed_id
        wm.last_run_at = utcnow_naive()
        wm.last_run_rows_affected = rows_affected
        wm.last_error = error
        wm.last_error_detail = None if error is None else "see audit_events"
        await db.commit()


# ── Daily physical purge ──────────────────────────────────────
async def _purge_job_runs(*, dry_run: bool) -> dict[str, Any]:
    """Apply the M4.6 per-row retention for ``job_runs``.

    Returns a payload shaped like the standard ``PurgeReport`` dict so
    the cron can fold it into the same audit metadata. ``failed`` /
    ``failed_permanent`` rows are intentionally never deleted.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            from app.services import job_run as job_run_svc

            candidates, deleted = await job_run_svc.purge_expired_success_rows(
                db, older_than=_JOB_RUNS_SUCCESS_TTL, dry_run=dry_run
            )
            await db.commit()
        return {
            "candidates": int(candidates),
            "deleted": int(deleted),
            "skipped_reason": None,
            "policy": "per_row_success_60d_failure_kept",
        }
    except Exception as exc:
        log.warning("job_runs per-row purge failed: %s", exc)
        return {
            "candidates": 0,
            "deleted": 0,
            "skipped_reason": f"error:{type(exc).__name__}",
            "policy": "per_row_success_60d_failure_kept",
        }


async def retention_physical_purge(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron: physically delete rows past their retention window.

    Defaults to dry-run; an admin enables hard delete via
    ``system_settings.retention.physical_purge_enabled = True``. Either
    way the result is mirrored to ``audit_events`` so the
    operator can compare purge candidates to actual deletes.
    """
    factory = get_session_factory()
    async with factory() as db:
        settings = await retention_svc.get_retention_settings(db)
    enabled = bool(settings.physical_purge_enabled)

    async with factory() as db:
        report = await retention_svc.physically_purge_expired(db, dry_run=not enabled)
        per_table = {
            name: {
                "candidates": rep.candidates,
                "deleted": rep.deleted,
                "skipped_reason": rep.skipped_reason,
            }
            for name, rep in report.items()
        }
        # M4.6 — bolt the per-row job_runs purge onto the same report
        # so the dashboard sees one consistent payload.
        job_runs_report = await _purge_job_runs(dry_run=not enabled)
        per_table["job_runs"] = job_runs_report
        total_candidates = sum(rep.candidates for rep in report.values()) + int(
            job_runs_report["candidates"]
        )
        total_deleted = sum(rep.deleted for rep in report.values()) + int(
            job_runs_report["deleted"]
        )
        await audit_svc.record(
            db,
            action="data.physical_purge",
            actor_identity_id=None,
            workspace_id=None,
            resource_type="retention",
            resource_id=None,
            summary=(
                ("would purge " if not enabled else "purged ")
                + f"{total_candidates if not enabled else total_deleted} rows "
                f"across {len(report) + 1} tables"
            ),
            metadata={
                "dry_run": not enabled,
                "tables": per_table,
                "totals": {
                    "candidates": total_candidates,
                    "deleted": total_deleted,
                },
            },
        )
        if enabled:
            await db.commit()
        else:
            await db.commit()
    return {
        "dry_run": not enabled,
        "tables": per_table,
        "totals": {
            "candidates": total_candidates,
            "deleted": total_deleted,
        },
    }


# ── Permanent-failure hook ───────────────────────────────────
async def on_retention_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """ARQ ``on_job_end`` integration: log a permanent failure once."""
    try:
        function_name = ctx.get("function") or "retention"
        job_id = ctx.get("job_id")
        async with get_session_factory()() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=f"job {function_name} failed permanently: {exc!r}",
                metadata={
                    "function": function_name,
                    "exception": repr(exc),
                },
            )
            try:
                from app.services import notification_events as notif_events

                await notif_events.emit_event(
                    db,
                    event_key="job.failed_permanent",
                    workspace_id=None,
                    cooldown_resource_id=str(job_id) if job_id else function_name,
                    payload={
                        "function": function_name,
                        "job_id": str(job_id) if job_id else None,
                        "exception": repr(exc)[:200],
                    },
                )
            except Exception:  # pragma: no cover
                log.exception(
                    "notify job.failed_permanent failed for %s", function_name
                )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("retention permanent-failure hook crashed")


__all__ = [
    "on_retention_job_failed_permanent",
    "retention_physical_purge",
    "retention_sweep_cascade",
]
