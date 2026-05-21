"""Admin Background Job Observability API (M4.6).

Five endpoints, all gated through the same auth helper that allows
either a platform admin (cross-workspace) or a workspace admin scoped
to their own ``X-Workspace-Id`` header. Workspace admins never see
rows belonging to other tenants — the repository-level filter sits in
``list_recent_job_runs`` / ``get_queue_health``.

* ``GET    /admin/jobs/queues``    — per-function counters + Redis
  queue depth.
* ``GET    /admin/jobs/recent``    — most recent rows
  (``status=failed_permanent`` is the third-strike feed).
* ``GET    /admin/jobs/health``    — totals headline.
* ``POST   /admin/jobs/{job_id}/retry`` — platform-admin-only manual
  re-enqueue (workspace admins read but can't fire).

Audit:

* ``job.retry_triggered_by_admin`` — every successful retry call.
* ``job.middleware_record_failed`` — defensive — used by
  :mod:`app.services.job_run` when the metadata write itself fails.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId, DBSession
from app.core.config import settings
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity, PlatformRole
from app.db.models.job_run import JobRun, JobRunStatus
from app.repositories.identity import IdentityRepository
from app.services import audit as audit_svc
from app.services import job_run as job_run_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/jobs", tags=["admin", "jobs"])


# Default ARQ queue name — see https://arq-docs.helpmanual.io/.
ARQ_DEFAULT_QUEUE = "arq:queue"


# ── DTOs ──────────────────────────────────────────────────────
class JobRunRead(BaseModel):
    id: uuid.UUID
    job_id: str
    function_name: str
    workspace_id: uuid.UUID | None
    identity_id: uuid.UUID | None
    status: JobRunStatus
    enqueued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    retry_count: int
    args_json: dict[str, Any] = Field(default_factory=dict)
    error_class: str | None
    error_message: str | None
    created_at: datetime


class FunctionStatsRow(BaseModel):
    function_name: str
    queued: int = 0
    running: int = 0
    success: int = 0
    failed: int = 0
    failed_permanent: int = 0


class QueueDepth(BaseModel):
    queue_name: str
    depth: int | None
    error: str | None = None


class QueuesResponse(BaseModel):
    window_seconds: int
    by_function: list[FunctionStatsRow]
    redis_queue: QueueDepth


class HealthTotals(BaseModel):
    queued: int = 0
    running: int = 0
    success: int = 0
    failed: int = 0
    failed_permanent: int = 0
    failed_permanent_total: int = 0


class HealthResponse(BaseModel):
    window_started_at: datetime
    window_seconds: int
    totals: HealthTotals
    by_function: dict[str, dict[str, int]]


class RetryResponse(BaseModel):
    enqueued: bool
    new_job_id: str | None
    function_name: str | None


# ── Auth gate ────────────────────────────────────────────────
async def _resolve_workspace_scope(
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
    *,
    cross_workspace_required: bool = False,
) -> tuple[Identity, uuid.UUID | None]:
    """Return ``(identity, scope_workspace_id)``.

    * Platform admins always pass and receive ``scope_workspace_id=None``
      (cross-workspace scope).
    * Workspace admins must present an ``X-Workspace-Id`` header that
      they admin; the scope is set to that workspace id and every list
      / health query filters by it.
    * ``cross_workspace_required=True`` rejects workspace admins
      outright — used by the manual retry endpoint per the M4.6
      RBAC design table.
    """
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise HTTPException(status_code=401, detail="auth.no_identity")
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity, None
    if cross_workspace_required:
        raise HTTPException(
            status_code=403, detail="platform_admin_required"
        )
    raw = request.headers.get("X-Workspace-Id")
    if not raw:
        raise HTTPException(
            status_code=403, detail="workspace_id_required"
        )
    try:
        ws_id = uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="invalid_workspace_id"
        ) from e
    await ws_svc.ensure_admin(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    return identity, ws_id


# ── Helpers ──────────────────────────────────────────────────
def _to_read(row: JobRun) -> JobRunRead:
    return JobRunRead(
        id=row.id,
        job_id=row.job_id,
        function_name=row.function_name,
        workspace_id=row.workspace_id,
        identity_id=row.identity_id,
        status=row.status,
        enqueued_at=row.enqueued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        retry_count=row.retry_count,
        args_json=row.args_json or {},
        error_class=row.error_class,
        error_message=row.error_message,
        created_at=row.created_at,
    )


async def _redis_queue_depth() -> QueueDepth:
    """Query the ARQ default queue's ZSET cardinality.

    Returns ``depth=None`` + ``error=...`` when Redis is unreachable —
    the dashboard surfaces "Redis offline" instead of crashing.
    """
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            depth = await r.zcard(ARQ_DEFAULT_QUEUE)
        finally:
            await r.aclose()
        return QueueDepth(queue_name=ARQ_DEFAULT_QUEUE, depth=int(depth or 0))
    except Exception as exc:  # pragma: no cover - infra dependent
        log.warning("admin_jobs.queue_depth_failed err=%s", exc)
        return QueueDepth(
            queue_name=ARQ_DEFAULT_QUEUE, depth=None, error=str(exc)[:200]
        )


# ── Routes ───────────────────────────────────────────────────
@router.get(
    "/queues",
    response_model=QueuesResponse,
    dependencies=[
        Depends(rate_limit("admin_jobs_queues", limit=30, period_seconds=60))
    ],
)
async def get_queues(
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
    window_seconds: int = Query(default=3600, ge=60, le=86_400),
) -> QueuesResponse:
    _, ws_id = await _resolve_workspace_scope(db, identity_id, request)
    health = await job_run_svc.get_queue_health(
        db,
        workspace_id=ws_id,
        window=timedelta(seconds=window_seconds),
    )
    rows: list[FunctionStatsRow] = []
    for fn, bucket in (health.get("by_function") or {}).items():
        rows.append(
            FunctionStatsRow(
                function_name=fn,
                queued=bucket.get("queued", 0),
                running=bucket.get("running", 0),
                success=bucket.get("success", 0),
                failed=bucket.get("failed", 0),
                failed_permanent=bucket.get("failed_permanent", 0),
            )
        )
    rows.sort(key=lambda r: r.function_name)
    redis_depth = await _redis_queue_depth()
    return QueuesResponse(
        window_seconds=window_seconds,
        by_function=rows,
        redis_queue=redis_depth,
    )


@router.get(
    "/recent",
    response_model=list[JobRunRead],
    dependencies=[
        Depends(rate_limit("admin_jobs_recent", limit=60, period_seconds=60))
    ],
)
async def list_recent(
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    function_name: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[JobRunRead]:
    _, ws_id = await _resolve_workspace_scope(db, identity_id, request)
    parsed_status: JobRunStatus | None = None
    if status_filter:
        try:
            parsed_status = JobRunStatus(status_filter)
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"invalid_status:{status_filter}"
            ) from e
    rows = await job_run_svc.list_recent_job_runs(
        db,
        workspace_id=ws_id,
        status=parsed_status,
        function_name=function_name,
        limit=limit,
    )
    return [_to_read(r) for r in rows]


@router.get(
    "/health",
    response_model=HealthResponse,
    dependencies=[
        Depends(rate_limit("admin_jobs_health", limit=30, period_seconds=60))
    ],
)
async def get_health(
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
    window_seconds: int = Query(default=3600, ge=60, le=86_400),
) -> HealthResponse:
    _, ws_id = await _resolve_workspace_scope(db, identity_id, request)
    payload = await job_run_svc.get_queue_health(
        db,
        workspace_id=ws_id,
        window=timedelta(seconds=window_seconds),
    )
    totals = HealthTotals(**(payload.get("totals") or {}))
    return HealthResponse(
        window_started_at=payload["window_started_at"],
        window_seconds=int(payload["window_seconds"]),
        totals=totals,
        by_function=payload.get("by_function") or {},
    )


@router.post(
    "/{job_id}/retry",
    response_model=RetryResponse,
    dependencies=[
        Depends(rate_limit("admin_jobs_retry", limit=5, period_seconds=60))
    ],
)
async def retry_job(
    job_id: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> RetryResponse:
    """Re-enqueue a permanently-failed job (platform admin only).

    The original ``args`` / ``kwargs`` are reconstructed from the
    persisted ``args_json``; positional arguments live under
    ``"args"`` and kwargs under ``"kwargs"``. We deliberately re-fire
    the same function name + same payload so the dashboard's
    "manual retry" surface is a true reissue, not a configuration
    drift escape hatch.
    """
    admin, _ = await _resolve_workspace_scope(
        db, identity_id, request, cross_workspace_required=True
    )
    row = await job_run_svc.get_job_run(db, job_id=job_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail="job_not_found"
        )
    if row.status not in {
        JobRunStatus.FAILED,
        JobRunStatus.FAILED_PERMANENT,
    }:
        raise HTTPException(
            status_code=409, detail="job_not_eligible_for_retry"
        )
    payload = row.args_json or {}
    if payload.get("_truncated"):
        raise HTTPException(
            status_code=409, detail="args_truncated_cannot_replay"
        )
    args = list(payload.get("args") or ())
    kwargs = dict(payload.get("kwargs") or {})

    from app.worker import queue as queue_svc

    new_job_id = await queue_svc.enqueue(
        row.function_name,
        *args,
        _workspace_id=row.workspace_id,
        _identity_id=row.identity_id,
        **kwargs,
    )
    await audit_svc.record(
        db,
        action="job.retry_triggered_by_admin",
        actor_identity_id=admin.id,
        workspace_id=row.workspace_id,
        resource_type="job_run",
        resource_id=row.id,
        summary=(
            f"manual retry of {row.function_name} (original job_id={row.job_id})"
        ),
        metadata={
            "original_job_id": row.job_id,
            "new_job_id": new_job_id,
            "function_name": row.function_name,
            "original_status": str(row.status),
            "original_retry_count": int(row.retry_count or 0),
        },
        request=request,
    )
    await db.commit()
    _ = datetime.now(UTC)  # touch utc to keep import resolvable
    return RetryResponse(
        enqueued=new_job_id is not None,
        new_job_id=new_job_id,
        function_name=row.function_name,
    )
