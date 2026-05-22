"""Integration tests for the M4.6 ``job_runs`` per-row retention policy.

Asserts:

* :func:`app.services.job_run.purge_expired_success_rows` deletes
  ``status=success`` rows older than the 60-day TTL.
* The same helper leaves ``status=failed`` and ``status=failed_permanent``
  rows untouched, no matter how old.
* ``dry_run=True`` only counts candidates without deleting.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.job_run import JobRun, JobRunStatus
from app.repositories.job_run import JobRunRepository
from app.services import job_run as job_run_svc

pytestmark = pytest.mark.asyncio


async def _seed(
    db_session,
    *,
    status: JobRunStatus,
    finished_days_ago: int,
    function_name: str = "curator_tick",
    workspace_id: uuid.UUID | None = None,
) -> JobRun:
    row = JobRun(
        job_id=f"r-{uuid.uuid4().hex[:8]}",
        function_name=function_name,
        workspace_id=workspace_id,
        status=status,
        enqueued_at=utcnow_naive() - timedelta(days=finished_days_ago + 1),
        started_at=utcnow_naive() - timedelta(days=finished_days_ago + 1),
        finished_at=utcnow_naive() - timedelta(days=finished_days_ago),
        duration_ms=100,
        retry_count=0,
        args_json={},
    )
    db_session.add(row)
    await db_session.flush([row])
    await db_session.commit()
    return row


async def test_old_success_rows_purged(db_session, workspace):
    fresh = await _seed(
        db_session,
        status=JobRunStatus.SUCCESS,
        finished_days_ago=10,
        workspace_id=workspace.id,
    )
    aged = await _seed(
        db_session,
        status=JobRunStatus.SUCCESS,
        finished_days_ago=90,
        workspace_id=workspace.id,
    )

    candidates, deleted = await job_run_svc.purge_expired_success_rows(
        db_session, older_than=timedelta(days=60), dry_run=False
    )
    await db_session.commit()
    assert candidates == 1
    assert deleted == 1

    repo = JobRunRepository(db_session)
    assert await repo.get_by_job_id(job_id=aged.job_id) is None
    assert await repo.get_by_job_id(job_id=fresh.job_id) is not None


async def test_failure_rows_kept_indefinitely(db_session, workspace):
    very_old_failed = await _seed(
        db_session,
        status=JobRunStatus.FAILED,
        finished_days_ago=999,
        workspace_id=workspace.id,
    )
    very_old_perm = await _seed(
        db_session,
        status=JobRunStatus.FAILED_PERMANENT,
        finished_days_ago=999,
        workspace_id=workspace.id,
    )

    candidates, deleted = await job_run_svc.purge_expired_success_rows(
        db_session, older_than=timedelta(days=60), dry_run=False
    )
    await db_session.commit()
    # No candidates because both rows are failure-shaped.
    assert candidates == 0
    assert deleted == 0

    repo = JobRunRepository(db_session)
    assert await repo.get_by_job_id(job_id=very_old_failed.job_id) is not None
    assert await repo.get_by_job_id(job_id=very_old_perm.job_id) is not None


async def test_dry_run_counts_without_deleting(db_session, workspace):
    aged = await _seed(
        db_session,
        status=JobRunStatus.SUCCESS,
        finished_days_ago=120,
        workspace_id=workspace.id,
    )
    candidates, deleted = await job_run_svc.purge_expired_success_rows(
        db_session, older_than=timedelta(days=60), dry_run=True
    )
    assert candidates >= 1
    assert deleted == 0

    repo = JobRunRepository(db_session)
    assert await repo.get_by_job_id(job_id=aged.job_id) is not None
