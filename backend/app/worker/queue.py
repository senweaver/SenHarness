"""Enqueue helper used from FastAPI request paths.

Wraps ``arq.create_pool`` in a process-local cached instance so we don't
pay the connection cost on every chat turn. Failures are non-fatal: if
Redis is down we log and return ``None`` — the user-facing turn must
not fail because the judge queue is offline.

Side-effect: every successful enqueue mirrors the call into the M4.6
``job_runs`` table so the admin dashboard can show a queued line
before the worker picks the task up. The metadata write itself is
best-effort — see :mod:`app.services.job_run`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

_pool: Any = None
_pool_lock_initialised = False


async def _get_pool() -> Any:
    global _pool, _pool_lock_initialised
    if _pool is not None:
        return _pool
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
    except ImportError:  # pragma: no cover
        return None
    if not _pool_lock_initialised:
        _pool_lock_initialised = True
    try:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception as e:  # pragma: no cover - fail-open
        log.warning("arq pool unreachable (%s) — enqueue will be a no-op", e)
        _pool = None
    return _pool


async def enqueue(
    function: str,
    *args: Any,
    _defer_by: int | None = None,
    _workspace_id: uuid.UUID | None = None,
    _identity_id: uuid.UUID | None = None,
    **kwargs: Any,
) -> str | None:
    """Enqueue an ARQ job. Returns the job id, or ``None`` on failure.

    ``_defer_by`` is in seconds. ``_workspace_id`` / ``_identity_id``
    are pulled out of ``kwargs`` so the optional M4.6 metadata row
    can attribute the enqueue without forcing every caller to pass
    them through the task-side signature; the underscore prefix
    keeps them out of ARQ's own kwargs payload.
    """
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        from datetime import timedelta

        job_kwargs: dict[str, Any] = {}
        if _defer_by:
            job_kwargs["_defer_by"] = timedelta(seconds=int(_defer_by))
        job = await pool.enqueue_job(function, *args, **job_kwargs, **kwargs)
        if job is None:
            return None
        job_id = job.job_id
    except Exception as e:  # pragma: no cover
        log.warning("enqueue %s failed: %s", function, e)
        return None

    # Best-effort metadata write for the M4.6 dashboard. Imported
    # lazily to keep the request-path import graph thin and to
    # avoid a circular import via app.services -> app.worker.
    try:
        from app.services import job_run as job_run_svc

        await job_run_svc.record_job_enqueued(
            job_id=str(job_id),
            function_name=function,
            args=list(args),
            kwargs=kwargs,
            workspace_id=_workspace_id,
            identity_id=_identity_id,
        )
    except Exception as exc:  # pragma: no cover - svc swallows
        log.warning(
            "job_run.enqueue_metadata_failed function=%s err=%s",
            function,
            exc,
        )

    return job_id
