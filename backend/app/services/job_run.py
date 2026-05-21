"""Persistent ARQ job lifecycle service (M4.6 Background Job Observability).

Three callers wire into this module:

* :mod:`app.worker.queue` (the request-side ``enqueue`` helper) calls
  :func:`record_job_enqueued` immediately after ARQ accepts the
  ``enqueue_job`` so the dashboard reflects new work the instant it
  lands. Failure to record is logged + swallowed — the user-facing
  request must never break because the metadata write hit a snag.
* :mod:`app.worker.arq_middleware` (ARQ's ``on_job_start`` /
  ``on_job_end`` hooks) calls :func:`record_job_started` and
  :func:`record_job_finished` to flip the row through ``RUNNING`` →
  ``SUCCESS`` / ``FAILED`` / ``FAILED_PERMANENT``.
* :mod:`app.api.v1.admin_jobs` calls :func:`get_queue_health` to
  fetch the dashboard headline counters.

The middleware path opens a *fresh* ``AsyncSession`` per call so the
ARQ context's life cycle is independent of the FastAPI request-scope
session — same pattern as :mod:`app.jobs.judge` etc.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job_run import (
    ARGS_JSON_MAX_BYTES,
    ERROR_MESSAGE_MAX_CHARS,
    JobRunStatus,
)
from app.db.session import get_session_factory
from app.repositories.job_run import JobRunRepository

log = logging.getLogger(__name__)


# Field name fragments whose value must be redacted before being
# persisted into ``job_runs.args_json``. The match is **substring**,
# case-insensitive, mirroring the audit-side allowlist in
# :data:`app.services.platform_settings.SECRET_FIELD_NAMES`. We keep
# the list local instead of importing the audit helper because the
# job_run path runs from inside the ARQ worker process where the
# audit cache may not be primed.
SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "auth",
    "credential",
    "bearer",
    "cookie",
    "session_id",
    "x-api-key",
)


_REDACTED = "***"


def _is_sensitive_key(key: str) -> bool:
    lk = key.lower()
    return any(frag in lk for frag in SENSITIVE_KEY_FRAGMENTS)


def redact_args(payload: Any, *, depth: int = 0, max_depth: int = 6) -> Any:
    """Recursively replace values under sensitive-named keys with ``"***"``.

    Stops recursing past ``max_depth`` so a deeply self-referential
    payload can't blow the stack — beyond the cap we replace the
    sub-tree with ``"<truncated>"`` rather than raise.
    """
    if depth >= max_depth:
        return "<truncated>"
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if not isinstance(k, str):
                key_str = str(k)
            else:
                key_str = k
            if _is_sensitive_key(key_str) and v not in (None, ""):
                out[key_str] = _REDACTED
            else:
                out[key_str] = redact_args(v, depth=depth + 1, max_depth=max_depth)
        return out
    if isinstance(payload, (list, tuple)):
        return [
            redact_args(item, depth=depth + 1, max_depth=max_depth)
            for item in payload
        ]
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    if isinstance(payload, uuid.UUID):
        return str(payload)
    if isinstance(payload, datetime):
        return payload.isoformat()
    return str(payload)


def truncate_json_for_storage(payload: Any) -> dict[str, Any]:
    """Serialise + size-cap ``args_json`` to ``ARGS_JSON_MAX_BYTES``.

    The dashboard shows the body verbatim so we keep it as a JSON
    object even after the cap is hit; oversized bodies collapse to
    ``{"_truncated": true, "_size_bytes": N}`` so the UI can render a
    "payload too large" badge instead of dumping a partial JSON
    fragment that would fail to parse on the frontend.
    """
    if not isinstance(payload, dict):
        payload = {"_value": payload}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_unserialisable": True}
    if len(encoded.encode("utf-8")) > ARGS_JSON_MAX_BYTES:
        return {
            "_truncated": True,
            "_size_bytes": len(encoded.encode("utf-8")),
        }
    return payload


def truncate_error_message(text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) <= ERROR_MESSAGE_MAX_CHARS:
        return text
    return text[: ERROR_MESSAGE_MAX_CHARS - 16] + "…[truncated]"


def build_args_payload(
    args: Sequence[Any] | None,
    kwargs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project ARQ ``(args, kwargs)`` into a single JSONB-friendly object.

    Positional args land under ``"args"`` (verbatim list) and keyword
    args under ``"kwargs"`` (dict). Sensitive keys are redacted before
    the body is size-checked.
    """
    body: dict[str, Any] = {}
    if args:
        body["args"] = list(args)
    if kwargs:
        body["kwargs"] = dict(kwargs)
    return truncate_json_for_storage(redact_args(body))


def _now() -> datetime:
    """Naive UTC ``now()`` matching the column defaults."""
    return datetime.now(UTC).replace(tzinfo=None)


def _extract_workspace_id(
    args: Sequence[Any] | None,
    kwargs: Mapping[str, Any] | None,
) -> uuid.UUID | None:
    """Best-effort scrape of ``workspace_id`` from ARQ task args.

    Looks for the kwarg in ``kwargs`` first (every M0–M3 ARQ task
    that's workspace-scoped accepts it as a keyword); if absent, falls
    back to the conventional first positional argument when it parses
    as a UUID. Anything we can't parse becomes ``None`` and the row
    surfaces under "platform-wide" in the dashboard.
    """
    if kwargs:
        candidate = kwargs.get("workspace_id")
        if candidate is None:
            candidate = kwargs.get("ws_id")
        if isinstance(candidate, uuid.UUID):
            return candidate
        if isinstance(candidate, str):
            try:
                return uuid.UUID(candidate)
            except ValueError:
                pass
    if args:
        first = args[0]
        if isinstance(first, uuid.UUID):
            return first
        if isinstance(first, str):
            try:
                return uuid.UUID(first)
            except ValueError:
                return None
    return None


# ── Request-side: enqueue side ──────────────────────────────────
async def record_job_enqueued(
    *,
    job_id: str,
    function_name: str,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    workspace_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
    db_factory: Any | None = None,
) -> None:
    """Insert one ``QUEUED`` row. Best-effort.

    The caller is :func:`app.worker.queue.enqueue` which already
    swallows failures from the ARQ side — we honour the same
    invariant so a downed Postgres never bubbles into a request.
    """
    factory = db_factory or get_session_factory()
    workspace_id = workspace_id or _extract_workspace_id(args, kwargs)
    args_json = build_args_payload(args, kwargs)
    try:
        async with factory() as session:
            try:
                await JobRunRepository(session).upsert_queued(
                    job_id=job_id,
                    function_name=function_name,
                    args_json=args_json,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        log.warning(
            "job_run.record_enqueue_failed function=%s job_id=%s err=%s",
            function_name,
            job_id,
            exc,
        )


# ── Worker-side: middleware hooks ──────────────────────────────
async def record_job_started(
    *,
    job_id: str,
    function_name: str,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    started_at: datetime | None = None,
    workspace_id: uuid.UUID | None = None,
    db_factory: Any | None = None,
) -> None:
    factory = db_factory or get_session_factory()
    workspace_id = workspace_id or _extract_workspace_id(args, kwargs)
    args_json = build_args_payload(args, kwargs)
    try:
        async with factory() as session:
            try:
                await JobRunRepository(session).mark_running(
                    job_id=job_id,
                    function_name=function_name,
                    args_json=args_json,
                    workspace_id=workspace_id,
                    started_at=started_at or _now(),
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        log.warning(
            "job_run.record_started_failed function=%s job_id=%s err=%s",
            function_name,
            job_id,
            exc,
        )


async def record_job_finished(
    *,
    job_id: str,
    status: JobRunStatus,
    duration_ms: int | None,
    retry_count: int,
    finished_at: datetime | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    db_factory: Any | None = None,
) -> None:
    factory = db_factory or get_session_factory()
    try:
        async with factory() as session:
            try:
                await JobRunRepository(session).mark_finished(
                    job_id=job_id,
                    status=status,
                    finished_at=finished_at or _now(),
                    duration_ms=duration_ms,
                    retry_count=retry_count,
                    error_class=(error_class or None),
                    error_message=truncate_error_message(error_message),
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        log.warning(
            "job_run.record_finished_failed status=%s job_id=%s err=%s",
            status,
            job_id,
            exc,
        )


# ── Read-side: admin endpoints ─────────────────────────────────
DEFAULT_HEALTH_WINDOW = timedelta(hours=1)


async def get_queue_health(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    window: timedelta = DEFAULT_HEALTH_WINDOW,
) -> dict[str, Any]:
    """Headline counters for the Background Jobs dashboard.

    Always returns every key (zero-filled) so the frontend doesn't
    need defensive coalescing.
    """
    since = _now() - window
    repo = JobRunRepository(db)
    by_function = await repo.get_queue_stats(
        since=since, workspace_id=workspace_id
    )
    aggregate = await repo.aggregate_health(
        since=since, workspace_id=workspace_id
    )
    return {
        "window_started_at": since,
        "window_seconds": int(window.total_seconds()),
        "totals": aggregate,
        "by_function": by_function,
    }


async def list_recent_job_runs(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    status: JobRunStatus | None = None,
    function_name: str | None = None,
    limit: int = 200,
) -> Sequence[Any]:
    return await JobRunRepository(db).list_recent(
        workspace_id=workspace_id,
        status=status,
        function_name=function_name,
        limit=limit,
    )


async def get_job_run(db: AsyncSession, *, job_id: str) -> Any | None:
    return await JobRunRepository(db).get_by_job_id(job_id=job_id)


# ── Retention purge helper ─────────────────────────────────────
async def purge_expired_success_rows(
    db: AsyncSession,
    *,
    older_than: timedelta = timedelta(days=60),
    dry_run: bool = False,
) -> tuple[int, int]:
    """Hard-delete ``status=success`` rows older than ``older_than``.

    Returns ``(candidates, deleted)``. Failure rows are intentionally
    excluded — see :mod:`app.services.retention` for the
    per-table policy contract.
    """
    repo = JobRunRepository(db)
    cutoff = _now() - older_than
    candidates = await repo.count_purge_candidates(cutoff=cutoff)
    if dry_run or candidates == 0:
        return candidates, 0
    deleted = await repo.purge_expired_success(cutoff=cutoff)
    return candidates, deleted
