"""Persistent ARQ job lifecycle row (M4.6 Background Job Observability).

ARQ keeps job metadata in Redis with a TTL of a few minutes — too short
for an admin dashboard that needs to answer "did the curator pass run
this morning, how long did it take, and which workspace tripped a
permanent failure?".

Each ARQ task lifecycle event lands a single row here:

* ``QUEUED`` — written from the request path before
  ``redis.enqueue_job`` returns (best-effort; failure does not block
  the user-facing transaction).
* ``RUNNING`` — written by :mod:`app.worker.arq_middleware` at
  ``on_job_start`` time; promotes the prior ``QUEUED`` row.
* ``SUCCESS`` / ``FAILED`` / ``FAILED_PERMANENT`` — written at
  ``on_job_end``. ``FAILED`` covers a single attempt that raised but
  still has retry budget; ``FAILED_PERMANENT`` covers the third strike
  where the existing ``on_job_end`` dispatcher in
  :mod:`app.worker.arq_app` already records the per-task permanent
  failure audit.

Indices cover the three hot paths:

* ``ix_job_runs_function_status_finished`` — admin dashboard
  ``WHERE function_name=? AND status=? ORDER BY finished_at DESC``.
* ``ix_job_runs_workspace_finished`` — workspace-admin scoped
  recent feed.
* ``ix_job_runs_status`` (single column) — global queue stats.

Retention policy is **per-row**, not the standard
``retention_watermarks`` cascade: success rows expire after 60 days,
failure rows are kept indefinitely so post-mortem still works after
the next quarterly purge. See :func:`app.services.retention` for the
purge predicate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class JobRunStatus(StrEnum):
    """Lifecycle state of one ``JobRun`` row.

    ``FAILED`` and ``FAILED_PERMANENT`` are split so the dashboard can
    answer two distinct questions:
    "is the queue backed up by transient errors?" (FAILED, retried)
    vs "do we have rows that exhausted retry budget?"
    (FAILED_PERMANENT, drained from the queue but kept for forensics).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"


# Caps applied by :mod:`app.services.job_run` before persisting. The
# 4 KB cap on ``args_json`` and ``error_message`` keeps a runaway
# stack trace from filling the admin index; the JSON cap is measured
# on the serialised body so a deep dict still triggers the cut.
ARGS_JSON_MAX_BYTES: int = 4096
ERROR_MESSAGE_MAX_CHARS: int = 4096


class JobRun(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        Index(
            "ix_job_runs_function_status_finished",
            "function_name",
            "status",
            "finished_at",
        ),
        Index(
            "ix_job_runs_workspace_finished",
            "workspace_id",
            "finished_at",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    status: Mapped[JobRunStatus] = mapped_column(
        Enum(
            JobRunStatus,
            name="job_run_status",
            native_enum=False,
            length=32,
            validate_strings=True,
        ),
        nullable=False,
        default=JobRunStatus.QUEUED,
        server_default=JobRunStatus.QUEUED.value,
        index=True,
    )
    enqueued_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        nullable=True, index=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    args_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="'{}'::jsonb"
    )
    error_class: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
