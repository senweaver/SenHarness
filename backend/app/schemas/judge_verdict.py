"""Pydantic DTOs for M0.3 judge verdicts.

The ``JudgeVerdictRead`` shape is what the REST surface returns; the
``JudgeSessionSummary`` shape aggregates verdicts inside one session
for the chat-page debug drawer.
"""

from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped


class JudgeVerdictRead(Timestamped):
    workspace_id: uuid.UUID
    artifact_id: uuid.UUID
    score: int
    confidence: float
    rationale: str
    process_notes_json: list[str]
    error_kind_hint: str | None
    judged_by_model: str | None
    latency_ms: int | None
    degraded: bool


class JudgeSessionSummary(ORMModel):
    """Aggregated counts of verdicts in one session.

    ``unjudged`` separates "queued, not yet scored" from the three
    decision buckets so the UI can show "12 runs · 8 success · 2
    partial · 1 failure · 1 pending".
    """

    session_id: uuid.UUID
    total_artifacts: int = Field(ge=0)
    success: int = Field(ge=0)
    partial: int = Field(ge=0)
    failure: int = Field(ge=0)
    unjudged: int = Field(ge=0)
    degraded: int = Field(ge=0)
