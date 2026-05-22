"""BatchRun + BatchRunCase — replay harness for regression / A-B evaluation.

A ``BatchRun`` is a job record: pick an agent (the "candidate") and a list
of cases (each either a fresh prompt or a previously captured session /
checkpoint), run them in parallel-but-bounded, diff outputs against the
baseline, and surface per-case stats.

Kept deliberately simple — no scoring rubric, no LLM-as-judge — so the
first cut is deterministic and cheap. Future iterations can layer semantic
similarity or custom grading on top of ``diff_json``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class BatchRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchCaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchRun(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "batch_runs"
    __table_args__ = (Index("ix_batch_runs_workspace_status", "workspace_id", "status"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[BatchRunStatus] = mapped_column(
        String(16),
        default=BatchRunStatus.PENDING,
        nullable=False,
    )
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    stats_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class BatchRunCase(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "batch_run_cases"
    __table_args__ = (Index("ix_batch_run_cases_batch_status", "batch_run_id", "status"),)

    batch_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("batch_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)

    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_checkpoints.id", ondelete="SET NULL"),
        nullable=True,
    )
    replay_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[BatchCaseStatus] = mapped_column(
        String(16),
        default=BatchCaseStatus.PENDING,
        nullable=False,
    )
    baseline_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
