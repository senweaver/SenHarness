"""Pydantic DTOs for session-goal lock + alignment scores (M0.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped


class SessionGoalCreate(ORMModel):
    goal_text: str = Field(min_length=1, max_length=2000)
    success_criteria: list[str] = Field(default_factory=list, max_length=20)
    alignment_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    metadata_json: dict = Field(default_factory=dict)


class SessionGoalUpdate(ORMModel):
    """Patch payload — every field optional. ``None`` means leave alone."""

    goal_text: str | None = Field(default=None, min_length=1, max_length=2000)
    success_criteria: list[str] | None = Field(default=None, max_length=20)
    alignment_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata_json: dict | None = None


class SessionGoalRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    goal_text: str
    success_criteria: list[str]
    locked_by: uuid.UUID
    locked_at: datetime
    unlocked_at: datetime | None
    unlocked_by: uuid.UUID | None
    alignment_threshold: float
    metadata_json: dict


class GoalAlignmentScoreRead(Timestamped):
    workspace_id: uuid.UUID
    session_goal_id: uuid.UUID
    message_id: uuid.UUID
    score: float
    rationale: str | None
    judged_by_model: str | None
    flagged: bool
