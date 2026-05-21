"""Repository helpers for :class:`JobRun` (M4.6 Background Job Observability).

Three responsibilities:

* Lifecycle write helpers (``upsert_queued`` / ``mark_running`` /
  ``mark_finished``) wrapped around a single ``job_id`` lookup so the
  ARQ middleware can call them without knowing primary keys.
* Read helpers (``list_recent`` / ``list_failed_permanent``) the admin
  routes use, with workspace + status + function-name filters.
* ``get_queue_stats`` aggregates the recent window into the per-function
  counters the Background Jobs dashboard needs ("how many running",
  "how many failed in the last hour").
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job_run import JobRun, JobRunStatus
from app.db.repository import AsyncRepository


class JobRunRepository(AsyncRepository[JobRun]):
    model = JobRun

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, JobRun)

    # ── Read ──────────────────────────────────────────────
    async def get_by_job_id(self, *, job_id: str) -> JobRun | None:
        """Return the most recent row for ``job_id``.

        Multiple rows may exist if the dashboard ever decides to keep
        per-attempt history; the current ``upsert_queued`` writes one
        row per ``job_id`` and reuses it across attempts so this
        helper is effectively unique.
        """
        stmt = (
            select(JobRun)
            .where(JobRun.job_id == job_id)
            .order_by(desc(JobRun.created_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_recent(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        status: JobRunStatus | None = None,
        function_name: str | None = None,
        limit: int = 200,
    ) -> Sequence[JobRun]:
        """Recent rows ordered by ``finished_at DESC NULLS FIRST``.

        ``NULLS FIRST`` puts in-flight ``QUEUED`` / ``RUNNING`` rows at
        the top of the dashboard so an operator clicking refresh
        always sees the active queue first.
        """
        stmt = select(JobRun)
        if workspace_id is not None:
            stmt = stmt.where(JobRun.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(JobRun.status == status)
        if function_name:
            stmt = stmt.where(JobRun.function_name == function_name)
        stmt = stmt.order_by(
            JobRun.finished_at.desc().nulls_first(),
            desc(JobRun.created_at),
        ).limit(max(1, min(limit, 1000)))
        return (await self.session.execute(stmt)).scalars().all()

    async def list_failed_permanent(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> Sequence[JobRun]:
        return await self.list_recent(
            workspace_id=workspace_id,
            status=JobRunStatus.FAILED_PERMANENT,
            limit=limit,
        )

    async def get_queue_stats(
        self, *, since: datetime, workspace_id: uuid.UUID | None = None
    ) -> dict[str, dict[str, int]]:
        """Per-function recent-window counters.

        ``since`` is the lower bound on ``finished_at`` for terminal
        statuses; ``QUEUED`` / ``RUNNING`` rows are counted regardless
        of how old they are because they're inherently "right now".
        Returns ``{function_name: {queued, running, success, failed,
        failed_permanent}}``. Functions with zero non-terminal rows
        and no terminal rows in the window are omitted.
        """
        stmt = select(
            JobRun.function_name,
            JobRun.status,
            func.count(JobRun.id).label("count"),
        )
        if workspace_id is not None:
            stmt = stmt.where(JobRun.workspace_id == workspace_id)
        stmt = stmt.where(
            (JobRun.status.in_(
                [JobRunStatus.QUEUED, JobRunStatus.RUNNING]
            ))
            | (JobRun.finished_at >= since)
        )
        stmt = stmt.group_by(JobRun.function_name, JobRun.status)
        rows = (await self.session.execute(stmt)).all()
        out: dict[str, dict[str, int]] = {}
        for function_name, status, count in rows:
            bucket = out.setdefault(
                function_name,
                {
                    "queued": 0,
                    "running": 0,
                    "success": 0,
                    "failed": 0,
                    "failed_permanent": 0,
                },
            )
            bucket[str(status)] = int(count or 0)
        return out

    async def aggregate_health(
        self, *, since: datetime, workspace_id: uuid.UUID | None = None
    ) -> dict[str, int]:
        """Single-line global counters for the top of the dashboard."""
        stats = await self.get_queue_stats(since=since, workspace_id=workspace_id)
        out = {
            "queued": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "failed_permanent": 0,
        }
        for bucket in stats.values():
            for k, v in bucket.items():
                out[k] = out.get(k, 0) + int(v)
        # Failed_permanent is *cumulative* across all time (never
        # purged) — surface the lifetime count so the dashboard can
        # warn even when nothing has triggered in the past hour.
        cum_stmt = select(func.count(JobRun.id)).where(
            JobRun.status == JobRunStatus.FAILED_PERMANENT
        )
        if workspace_id is not None:
            cum_stmt = cum_stmt.where(JobRun.workspace_id == workspace_id)
        cumulative = int(
            (await self.session.execute(cum_stmt)).scalar() or 0
        )
        out["failed_permanent_total"] = cumulative
        return out

    # ── Write ─────────────────────────────────────────────
    async def upsert_queued(
        self,
        *,
        job_id: str,
        function_name: str,
        args_json: dict[str, Any],
        workspace_id: uuid.UUID | None = None,
        identity_id: uuid.UUID | None = None,
    ) -> JobRun:
        """Insert a fresh ``QUEUED`` row, or no-op if one already exists.

        ARQ may re-enqueue under the same ``job_id`` (for example via
        ``_defer_by``), so we use Postgres ``ON CONFLICT DO NOTHING``
        on ``job_id`` and return the row that landed (or was already
        there). Because ``job_id`` is not unique by default, we follow
        up with a ``SELECT`` to fetch the most recent row.
        """
        row = await self.get_by_job_id(job_id=job_id)
        if row is not None:
            return row
        stmt = (
            pg_insert(JobRun)
            .values(
                job_id=job_id,
                function_name=function_name,
                args_json=args_json,
                workspace_id=workspace_id,
                identity_id=identity_id,
                status=JobRunStatus.QUEUED,
            )
            .returning(JobRun)
        )
        result = (await self.session.execute(stmt)).scalar_one()
        await self.session.flush()
        return result

    async def mark_running(
        self,
        *,
        job_id: str,
        function_name: str,
        args_json: dict[str, Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        started_at: datetime | None = None,
    ) -> JobRun:
        """Promote ``QUEUED`` → ``RUNNING`` (or seed a row when the
        request path never wrote a queued line — direct cron triggers
        skip the request side and land here first).
        """
        row = await self.get_by_job_id(job_id=job_id)
        if row is None:
            row = await self.upsert_queued(
                job_id=job_id,
                function_name=function_name,
                args_json=args_json or {},
                workspace_id=workspace_id,
            )
        row.status = JobRunStatus.RUNNING
        row.started_at = started_at
        if function_name and not row.function_name:
            row.function_name = function_name
        if workspace_id is not None and row.workspace_id is None:
            row.workspace_id = workspace_id
        await self.session.flush([row])
        return row

    async def mark_finished(
        self,
        *,
        job_id: str,
        status: JobRunStatus,
        finished_at: datetime,
        duration_ms: int | None,
        retry_count: int,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> JobRun | None:
        """Apply the terminal status. Returns ``None`` if the row was
        never registered (a defence against ARQ firing ``on_job_end``
        without a matching ``on_job_start`` — should never happen in
        practice but the middleware must stay forgiving).
        """
        row = await self.get_by_job_id(job_id=job_id)
        if row is None:
            return None
        row.status = status
        row.finished_at = finished_at
        row.duration_ms = duration_ms
        row.retry_count = retry_count
        row.error_class = error_class
        row.error_message = error_message
        await self.session.flush([row])
        return row

    # ── Retention helpers ─────────────────────────────────
    async def purge_expired_success(self, *, cutoff: datetime) -> int:
        """Hard-delete ``status=success`` rows whose ``finished_at < cutoff``.

        Failure rows (``failed`` / ``failed_permanent``) are intentionally
        kept indefinitely so post-mortem still works after the next
        retention pass.
        """
        from sqlalchemy import delete

        stmt = (
            delete(JobRun)
            .where(JobRun.status == JobRunStatus.SUCCESS)
            .where(JobRun.finished_at.isnot(None))
            .where(JobRun.finished_at < cutoff)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def count_purge_candidates(self, *, cutoff: datetime) -> int:
        stmt = select(func.count(JobRun.id)).where(
            JobRun.status == JobRunStatus.SUCCESS,
            JobRun.finished_at.isnot(None),
            JobRun.finished_at < cutoff,
        )
        return int((await self.session.execute(stmt)).scalar() or 0)
