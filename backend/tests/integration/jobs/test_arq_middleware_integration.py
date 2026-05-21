"""Integration tests for the M4.6 ARQ ``job_runs`` middleware.

Drives :func:`job_run_middleware_start` /
:func:`job_run_middleware_end` directly with a synthesised ARQ
``ctx`` dict and asserts the persistent ``JobRun`` row reaches the
expected terminal status. Skips cleanly when Postgres is unavailable
(via the ``db_session`` fixture's ``db_available`` gate).

The tests deliberately bypass the full ARQ runtime; the real worker
chain is exercised by the deployed backend in ``app/worker/arq_app.py``,
and a live-ARQ test would require a Redis container that isn't on
every contributor's machine.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.job_run import JobRunStatus
from app.repositories.job_run import JobRunRepository
from app.worker import arq_middleware as mw

pytestmark = pytest.mark.asyncio


async def _ctx_for(
    *,
    job_id: str,
    function_name: str,
    args: tuple = (),
    kwargs: dict | None = None,
    job_try: int = 1,
    max_tries: int = 3,
    exception: BaseException | None = None,
) -> dict:
    return {
        "job_id": job_id,
        "function": function_name,
        "args": args,
        "kwargs": kwargs or {},
        "job_try": job_try,
        "max_tries": max_tries,
        "exception": exception,
    }


async def test_start_then_end_success_flow(db_session, workspace):
    job_id = f"int-{uuid.uuid4().hex[:8]}"
    ctx = await _ctx_for(
        job_id=job_id,
        function_name="judge_session_artifact",
        kwargs={"workspace_id": str(workspace.id), "artifact_id": "a"},
    )
    await mw.job_run_middleware_start(ctx)
    await mw.job_run_middleware_end(ctx)

    repo = JobRunRepository(db_session)
    row = await repo.get_by_job_id(job_id=job_id)
    assert row is not None
    assert row.status == JobRunStatus.SUCCESS
    assert row.workspace_id == workspace.id
    assert row.duration_ms is not None and row.duration_ms >= 0


async def test_failed_permanent_when_max_tries_reached(db_session):
    job_id = f"int-{uuid.uuid4().hex[:8]}"
    err = RuntimeError("boom")
    ctx = await _ctx_for(
        job_id=job_id,
        function_name="curator_tick",
        job_try=3,
        max_tries=3,
        exception=err,
    )
    await mw.job_run_middleware_start(ctx)
    await mw.job_run_middleware_end(ctx)

    repo = JobRunRepository(db_session)
    row = await repo.get_by_job_id(job_id=job_id)
    assert row is not None
    assert row.status == JobRunStatus.FAILED_PERMANENT
    assert row.error_class == "RuntimeError"
    assert row.error_message == "boom"


async def test_failed_with_remaining_retries(db_session):
    job_id = f"int-{uuid.uuid4().hex[:8]}"
    ctx = await _ctx_for(
        job_id=job_id,
        function_name="hub_auto_pull_sweep",
        job_try=1,
        max_tries=3,
        exception=ValueError("transient"),
    )
    await mw.job_run_middleware_start(ctx)
    await mw.job_run_middleware_end(ctx)

    repo = JobRunRepository(db_session)
    row = await repo.get_by_job_id(job_id=job_id)
    assert row is not None
    assert row.status == JobRunStatus.FAILED


async def test_args_kwargs_are_redacted_before_persisting(db_session, workspace):
    job_id = f"int-{uuid.uuid4().hex[:8]}"
    ctx = await _ctx_for(
        job_id=job_id,
        function_name="some_task",
        kwargs={
            "workspace_id": str(workspace.id),
            "api_key": "sk-very-secret",
            "config": {"client_secret": "shhh", "name": "ok"},
        },
    )
    await mw.job_run_middleware_start(ctx)
    await mw.job_run_middleware_end(ctx)

    repo = JobRunRepository(db_session)
    row = await repo.get_by_job_id(job_id=job_id)
    assert row is not None
    persisted = row.args_json
    # The whole payload may collapse to a sentinel when oversized; for
    # this small body it should stay structured + redacted.
    assert persisted.get("kwargs", {}).get("api_key") == "***"
    assert (
        persisted.get("kwargs", {}).get("config", {}).get("client_secret")
        == "***"
    )
    assert (
        persisted.get("kwargs", {}).get("config", {}).get("name") == "ok"
    )


async def test_end_without_start_does_not_raise(db_session):
    """``on_job_end`` after a missing ``on_job_start`` is harmless.

    The middleware logs and continues; no row gets written but the
    ARQ runtime never sees the metadata error bubble up.
    """
    ctx = await _ctx_for(
        job_id=f"int-{uuid.uuid4().hex[:8]}",
        function_name="never_started",
    )
    await mw.job_run_middleware_end(ctx)


async def test_missing_job_id_short_circuits(db_session):
    """An ARQ context without ``job_id`` must be a no-op.

    Defence: ARQ always populates ``job_id`` but defensive coding
    keeps a corrupted ctx from crashing the worker.
    """
    await mw.job_run_middleware_start({})
    await mw.job_run_middleware_end({})
