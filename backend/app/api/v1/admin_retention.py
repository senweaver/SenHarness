"""Platform-admin surface for the GDPR retention sweep (M0.11).

Four endpoints, all gated by ``platform_admin`` role + per-bucket rate
limit:

* ``GET    /admin/retention/watermarks``     — current sweep cursor + last error
* ``GET    /admin/retention/last-runs``      — recent sweep / purge audit rows
* ``POST   /admin/retention/sweep/run``      — enqueue an out-of-band sweep tick
* ``POST   /admin/retention/purge/dry-run``  — force a dry-run purge report

The sweep itself runs as an ARQ cron (see
``app.worker.arq_app``); these endpoints are debugging surfaces, not
the operational hot path. Every mutation writes ``admin.retention.*``
audit rows so an external reviewer can correlate manual triggers with
their effects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import DBSession
from app.api.v1.admin import AdminGate
from app.core.rate_limit import rate_limit
from app.db.models.audit import AuditEvent
from app.db.models.identity import Identity
from app.db.models.retention_watermark import (
    RetentionScopeKind,
    RetentionWatermark,
)
from app.services import audit as audit_svc
from app.services import retention as retention_svc
from app.services.system_settings import RetentionSettings
from app.worker import queue as queue_svc

router = APIRouter(
    prefix="/admin/retention",
    tags=["admin", "retention"],
)


# ── DTOs ──────────────────────────────────────────────────────
class WatermarkRead(BaseModel):
    scope_kind: RetentionScopeKind
    last_seen_deleted_at: datetime
    last_processed_id_hash: str | None = Field(
        default=None,
        description=(
            "First 16 hex chars of SHA-256(last_processed_id). Raw UUID is "
            "never returned so the audit trail stays leak-free even when "
            "the admin UI is screen-shared."
        ),
    )
    last_run_at: datetime | None
    last_run_rows_affected: int
    last_error: str | None
    has_pending_failure: bool


class SweepStatus(BaseModel):
    watermarks: list[WatermarkRead]
    settings: RetentionSettings


class SweepTriggerResult(BaseModel):
    enqueued: bool
    job_id: str | None


class PurgeReportRow(BaseModel):
    table_name: str
    candidates: int
    deleted: int
    cutoff: datetime | None
    skipped_reason: str | None = None


class PurgeReportResponse(BaseModel):
    dry_run: bool
    rows: list[PurgeReportRow]
    total_candidates: int


class AuditRunRow(BaseModel):
    id: str
    action: str
    summary: str | None
    metadata: dict[str, Any]
    created_at: datetime


# ── Routes ────────────────────────────────────────────────────
@router.get(
    "/watermarks",
    response_model=SweepStatus,
    dependencies=[Depends(rate_limit("admin_retention_read", limit=30, period_seconds=60))],
)
async def get_watermarks(
    db: DBSession, _admin: Identity = AdminGate
) -> SweepStatus:
    rows = (
        await db.execute(
            select(RetentionWatermark).order_by(RetentionWatermark.scope_kind)
        )
    ).scalars().all()
    settings = await retention_svc.get_retention_settings(db)
    out: list[WatermarkRead] = []
    for row in rows:
        out.append(
            WatermarkRead(
                scope_kind=row.scope_kind,
                last_seen_deleted_at=row.last_seen_deleted_at,
                last_processed_id_hash=(
                    retention_svc.scope_id_hash(row.last_processed_id)
                    if row.last_processed_id
                    else None
                ),
                last_run_at=row.last_run_at,
                last_run_rows_affected=row.last_run_rows_affected,
                last_error=row.last_error,
                has_pending_failure=bool(row.last_error),
            )
        )
    return SweepStatus(watermarks=out, settings=settings)


@router.get(
    "/last-runs",
    response_model=list[AuditRunRow],
    dependencies=[Depends(rate_limit("admin_retention_read", limit=30, period_seconds=60))],
)
async def get_last_runs(
    db: DBSession, _admin: Identity = AdminGate, limit: int = 50
) -> list[AuditRunRow]:
    """Recent ``data.cascade_soft_delete`` / ``data.physical_purge`` audit rows."""
    bounded = max(1, min(limit, 200))
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.action.in_(
                [
                    "data.cascade_soft_delete",
                    "data.physical_purge",
                ]
            )
        )
        .order_by(desc(AuditEvent.created_at))
        .limit(bounded)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        AuditRunRow(
            id=str(row.id),
            action=row.action,
            summary=row.summary,
            metadata=row.metadata_json or {},
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/sweep/run",
    response_model=SweepTriggerResult,
    dependencies=[
        Depends(rate_limit("admin_retention_trigger", limit=5, period_seconds=300))
    ],
)
async def trigger_sweep(
    request: Request,
    db: DBSession,
    admin: Identity = AdminGate,
) -> SweepTriggerResult:
    """Enqueue a one-off ``retention_sweep_cascade`` run for debugging.

    Returns ``enqueued=False`` if the ARQ pool is unreachable — useful
    so the admin UI can surface a stale-Redis warning rather than
    silently swallowing the trigger.
    """
    job_id = await queue_svc.enqueue("retention_sweep_cascade")
    await audit_svc.record(
        db,
        action="admin.retention.sweep_trigger",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="retention",
        resource_id=None,
        summary=f"manual sweep trigger; job_id={job_id}",
        metadata={"job_id": job_id},
        request=request,
    )
    await db.commit()
    return SweepTriggerResult(enqueued=job_id is not None, job_id=job_id)


@router.post(
    "/purge/dry-run",
    response_model=PurgeReportResponse,
    dependencies=[
        Depends(rate_limit("admin_retention_trigger", limit=5, period_seconds=300))
    ],
)
async def purge_dry_run(
    request: Request,
    db: DBSession,
    admin: Identity = AdminGate,
) -> PurgeReportResponse:
    """Produce a dry-run purge report on demand.

    Always dry-run regardless of ``physical_purge_enabled``; the live
    delete path stays bound to the daily cron so an accidental click
    here can never destroy data.
    """
    report = await retention_svc.physically_purge_expired(db, dry_run=True)
    rows = [
        PurgeReportRow(
            table_name=rep.table_name,
            candidates=rep.candidates,
            deleted=rep.deleted,
            cutoff=rep.cutoff,
            skipped_reason=rep.skipped_reason,
        )
        for rep in report.values()
    ]
    total = sum(r.candidates for r in rows)
    await audit_svc.record(
        db,
        action="admin.retention.purge_dry_run",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="retention",
        resource_id=None,
        summary=f"dry-run purge: would delete {total} rows across {len(rows)} tables",
        metadata={
            "total_candidates": total,
            "tables": {
                r.table_name: {
                    "candidates": r.candidates,
                    "skipped_reason": r.skipped_reason,
                }
                for r in rows
            },
        },
        request=request,
    )
    await db.commit()
    return PurgeReportResponse(
        dry_run=True, rows=rows, total_candidates=total
    )
