"""Unit tests for :func:`JobRunRepository.get_queue_stats` (M4.6).

Seeds a mix of statuses across two function names + two workspaces and
verifies the per-function counters match the expected partition. Also
covers the ``aggregate_health`` helper used by the dashboard headline.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.job_run import JobRun, JobRunStatus
from app.repositories.job_run import JobRunRepository

pytestmark = pytest.mark.asyncio


async def _seed(
    db_session,
    *,
    function_name: str,
    status: JobRunStatus,
    minutes_ago: int,
    workspace_id: uuid.UUID | None,
) -> JobRun:
    now = utcnow_naive()
    finished_at = now - timedelta(minutes=minutes_ago) if status in {
        JobRunStatus.SUCCESS,
        JobRunStatus.FAILED,
        JobRunStatus.FAILED_PERMANENT,
    } else None
    started_at = (
        now - timedelta(minutes=minutes_ago, seconds=1)
        if status != JobRunStatus.QUEUED
        else None
    )
    row = JobRun(
        job_id=f"j-{uuid.uuid4().hex[:8]}",
        function_name=function_name,
        workspace_id=workspace_id,
        status=status,
        enqueued_at=now - timedelta(minutes=minutes_ago + 1),
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=100 if finished_at else None,
        retry_count=0,
        args_json={},
    )
    db_session.add(row)
    await db_session.flush([row])
    return row


async def test_get_queue_stats_partitions_by_function_and_status(
    db_session, workspace
):
    repo = JobRunRepository(db_session)
    cutoff = utcnow_naive() - timedelta(minutes=30)

    # Two sweeps for "judge_session_artifact" — both SUCCESS, in window.
    for _ in range(2):
        await _seed(
            db_session,
            function_name="judge_session_artifact",
            status=JobRunStatus.SUCCESS,
            minutes_ago=10,
            workspace_id=workspace.id,
        )
    # One running and one failed for "curator_tick".
    await _seed(
        db_session,
        function_name="curator_tick",
        status=JobRunStatus.RUNNING,
        minutes_ago=0,
        workspace_id=workspace.id,
    )
    await _seed(
        db_session,
        function_name="curator_tick",
        status=JobRunStatus.FAILED,
        minutes_ago=5,
        workspace_id=workspace.id,
    )
    # One failed_permanent for "evolver" outside the 30 min window —
    # must NOT show up in the windowed counts.
    await _seed(
        db_session,
        function_name="evolver_workspace_sweep",
        status=JobRunStatus.FAILED_PERMANENT,
        minutes_ago=120,
        workspace_id=workspace.id,
    )

    stats = await repo.get_queue_stats(
        since=cutoff, workspace_id=workspace.id
    )
    assert stats["judge_session_artifact"]["success"] == 2
    assert stats["curator_tick"]["running"] == 1
    assert stats["curator_tick"]["failed"] == 1
    # Older permanent failure isn't in the in-window stats —
    # `evolver_workspace_sweep` should not appear at all here.
    assert "evolver_workspace_sweep" not in stats


async def test_aggregate_health_includes_lifetime_failed_permanent(
    db_session, workspace
):
    repo = JobRunRepository(db_session)

    # Old permanent failure — outside the 1h window.
    await _seed(
        db_session,
        function_name="hub_auto_pull_sweep",
        status=JobRunStatus.FAILED_PERMANENT,
        minutes_ago=600,
        workspace_id=workspace.id,
    )
    # Recent success — inside the 1h window.
    await _seed(
        db_session,
        function_name="judge_session_artifact",
        status=JobRunStatus.SUCCESS,
        minutes_ago=15,
        workspace_id=workspace.id,
    )

    aggregate = await repo.aggregate_health(
        since=utcnow_naive() - timedelta(hours=1),
        workspace_id=workspace.id,
    )
    # Successes counted in window.
    assert aggregate["success"] >= 1
    # Lifetime permanent failures counted regardless of window.
    assert aggregate["failed_permanent_total"] >= 1


async def test_get_queue_stats_filters_by_workspace(db_session, workspace):
    repo = JobRunRepository(db_session)

    other_ws_id = uuid.uuid4()
    # "Other workspace" row that the workspace-scoped query must hide.
    # We deliberately use a UUID that doesn't exist as a workspace —
    # the repository filter is column-equality, not FK-resolved, so
    # the row simply lives under "another tenant".
    await _seed(
        db_session,
        function_name="curator_tick",
        status=JobRunStatus.SUCCESS,
        minutes_ago=10,
        workspace_id=other_ws_id,
    )
    await _seed(
        db_session,
        function_name="curator_tick",
        status=JobRunStatus.SUCCESS,
        minutes_ago=10,
        workspace_id=workspace.id,
    )

    scoped = await repo.get_queue_stats(
        since=utcnow_naive() - timedelta(hours=1),
        workspace_id=workspace.id,
    )
    cross = await repo.get_queue_stats(
        since=utcnow_naive() - timedelta(hours=1),
    )
    assert scoped["curator_tick"]["success"] == 1
    assert cross["curator_tick"]["success"] == 2
