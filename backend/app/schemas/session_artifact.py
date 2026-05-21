"""Pydantic DTOs for the per-run session artifact (M0.2).

Two surface shapes:

* :class:`ArtifactTurn` — one folded turn inside ``turns_json``. The
  ``message_id`` is the canonical pointer back to the ``messages``
  table; downstream PRM / Curator / Evolver code must keep that chain
  intact when summarising or archiving so a flagged artifact can always
  be resolved back to the raw transcript.
* :class:`SessionArtifactRead` — the read model returned by the REST
  surface. Excludes the soft-delete column so a tombstoned row is never
  exposed via the API; the GDPR cascade purge in M0.11 hard-deletes
  rows past their retention window.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class ArtifactOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class ArtifactTurn(ORMModel):
    """One folded turn in a captured run.

    ``message_id`` is the canonical pointer back to the ``messages``
    table; never break this chain — downstream evaluators rely on it
    to fetch the raw transcript when an artifact is flagged.
    """

    role: TurnRole
    text: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    tool_results: list[dict] = Field(default_factory=list)
    thinking: str | None = None
    iteration: int = Field(ge=0)
    message_id: uuid.UUID | None = None
    timestamp: datetime | None = None


class SessionArtifactRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID
    agent_id: uuid.UUID | None
    identity_id: uuid.UUID | None
    user_text_hash: str
    turns_json: list[ArtifactTurn]
    injected_skill_pack_ids: list[str]
    invoked_tools: list[str]
    iteration_count: int
    final_outcome: str
    error_kind: str | None
    judge_score: float | None
    goal_alignment_avg: float | None
    finished_at: datetime
