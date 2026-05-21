"""DTOs for session checkpoints + batch-replay runs (D21)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.db.models.batch import BatchCaseStatus, BatchRunStatus
from app.schemas._base import ORMModel, Timestamped


# ─── Session checkpoints ─────────────────────────────────
class SessionCheckpointCreate(ORMModel):
    label: str = Field(min_length=1, max_length=128)
    description: str | None = None


class SessionCheckpointRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    label: str
    description: str | None
    message_count: int
    snapshot_json: dict
    created_by: uuid.UUID | None


class SessionForkIn(ORMModel):
    """Fork a session at a checkpoint, optionally overriding the title."""

    checkpoint_id: uuid.UUID
    title: str | None = None


class SessionForkOut(ORMModel):
    original_session_id: uuid.UUID
    fork_session_id: uuid.UUID
    copied_message_count: int


# ─── Batch runs ──────────────────────────────────────────
class BatchCaseIn(ORMModel):
    """One case in the batch. Exactly one of ``text`` / ``source_session_id``
    / ``checkpoint_id`` must be non-null; the service validates that."""

    label: str | None = None
    text: str | None = None
    source_session_id: uuid.UUID | None = None
    checkpoint_id: uuid.UUID | None = None


class BatchRunCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    agent_id: uuid.UUID
    cases: list[BatchCaseIn] = Field(default_factory=list, min_length=1)
    config_json: dict[str, Any] = Field(default_factory=dict)


class BatchRunCaseRead(Timestamped):
    workspace_id: uuid.UUID
    batch_run_id: uuid.UUID
    case_label: str | None
    input_text: str
    source_session_id: uuid.UUID | None
    checkpoint_id: uuid.UUID | None
    replay_session_id: uuid.UUID | None
    status: BatchCaseStatus
    baseline_text: str | None
    output_text: str | None
    diff_json: dict
    error: str | None
    duration_ms: int | None


class BatchRunRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    agent_id: uuid.UUID | None
    status: BatchRunStatus
    config_json: dict
    stats_json: dict
    started_at: datetime | None
    finished_at: datetime | None
    created_by: uuid.UUID | None


class BatchRunDetail(BatchRunRead):
    cases: list[BatchRunCaseRead] = Field(default_factory=list)
