"""ARQ ``on_job_start`` / ``on_job_end`` hooks for M4.6 observability.

Two responsibilities:

* :func:`job_run_middleware_start` — flips the per-task ``JobRun`` row
  to ``RUNNING`` and records ``started_at`` / extracted
  ``workspace_id``. Best-effort: a record-side failure is logged but
  never raised back into the ARQ runtime, so a hiccup on the metadata
  side never breaks an actual judge / curator / verifier sweep.
* :func:`job_run_middleware_end` — applies the terminal status
  (``SUCCESS`` on a clean exit, ``FAILED`` for a retryable raise,
  ``FAILED_PERMANENT`` once ``job_try == max_tries``). Chains *behind*
  the existing :func:`app.worker.arq_app.on_job_end` so the per-task
  permanent-failure dispatcher (judge / retention / pending_memory /
  evolver / verifier / approval-TTL / subagent-zombie / inflight /
  agent-profile / user-modeling / hub-auto-pull) keeps firing — see
  ``ARQ_END_DISPATCH_CHAIN`` in :mod:`app.worker.arq_app`.

ARQ's context dictionary follows the v0.x shape:

* ``ctx["job_id"]`` — string job id (always present).
* ``ctx["function"]`` — qualified function name.
* ``ctx["args"]`` / ``ctx["kwargs"]`` — task arguments.
* ``ctx["score"]`` — enqueue epoch ms (start-time fallback).
* ``ctx["job_try"]`` — 1-indexed attempt counter; matches
  ``WorkerSettings.max_tries`` on the third strike.
* ``ctx["start_ms"]`` — set in :func:`job_run_middleware_start` so
  the end hook can measure ``duration_ms`` even when the underlying
  task didn't.
* ``ctx["exception"]`` — populated by ARQ when the task raised.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.db.models.job_run import JobRunStatus
from app.services import job_run as job_run_svc

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _extract_function_name(ctx: dict[str, Any]) -> str:
    raw = ctx.get("function") or ""
    if isinstance(raw, str):
        return raw.split(".")[-1] or raw
    name = getattr(raw, "__name__", None)
    if name:
        return str(name)
    return str(raw)


def _extract_job_id(ctx: dict[str, Any]) -> str | None:
    job_id = ctx.get("job_id")
    if isinstance(job_id, str) and job_id:
        return job_id
    if job_id is not None:
        return str(job_id)
    return None


async def job_run_middleware_start(ctx: dict[str, Any]) -> None:
    """Mark the task as ``RUNNING`` in the ``job_runs`` table.

    ``ctx`` is mutated so the end hook can measure duration even when
    the task itself doesn't track its own timing.
    """
    ctx["start_ms"] = _now_ms()
    job_id = _extract_job_id(ctx)
    if job_id is None:
        return
    function_name = _extract_function_name(ctx)
    args = ctx.get("args") or ()
    kwargs = ctx.get("kwargs") or {}
    try:
        await job_run_svc.record_job_started(
            job_id=job_id,
            function_name=function_name,
            args=args,
            kwargs=kwargs,
            started_at=_now(),
        )
    except Exception as exc:  # pragma: no cover - already swallowed inside svc
        log.warning(
            "job_run.middleware_start_failed function=%s job_id=%s err=%s",
            function_name,
            job_id,
            exc,
        )


async def job_run_middleware_end(ctx: dict[str, Any]) -> None:
    """Apply the terminal status to the matching ``JobRun`` row.

    Called *after* the existing ``on_job_end`` chain in
    :mod:`app.worker.arq_app` runs, so the per-task permanent-failure
    audit dispatcher stays intact.
    """
    job_id = _extract_job_id(ctx)
    if job_id is None:
        return
    function_name = _extract_function_name(ctx)
    exc = ctx.get("exception")
    job_try = int(ctx.get("job_try", 0) or 0)
    max_tries = int(ctx.get("max_tries", 0) or 0)
    if exc is None:
        status = JobRunStatus.SUCCESS
        error_class = None
        error_message = None
    elif max_tries > 0 and job_try >= max_tries:
        status = JobRunStatus.FAILED_PERMANENT
        error_class = type(exc).__name__
        error_message = str(exc)
    else:
        status = JobRunStatus.FAILED
        error_class = type(exc).__name__
        error_message = str(exc)

    started_ms = ctx.get("start_ms")
    duration_ms: int | None
    if isinstance(started_ms, int) and started_ms > 0:
        duration_ms = max(0, _now_ms() - started_ms)
    else:
        duration_ms = None

    try:
        await job_run_svc.record_job_finished(
            job_id=job_id,
            status=status,
            duration_ms=duration_ms,
            retry_count=max(0, job_try - 1),
            finished_at=_now(),
            error_class=error_class,
            error_message=error_message,
        )
    except Exception as inner:  # pragma: no cover - svc already swallows
        log.warning(
            "job_run.middleware_end_failed function=%s job_id=%s err=%s",
            function_name,
            job_id,
            inner,
        )
