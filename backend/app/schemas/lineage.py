"""Pydantic DTOs for the M4.3 lineage replay surface.

Two shapes:

* :class:`LineageNode` — one folded turn rendered in the replay drawer.
  ``text_excerpt`` is truncated to
  :data:`~app.db.models.message.LINEAGE_TEXT_EXCERPT_MAX_CHARS` chars
  so the drawer stays cheap to render even when a turn was a 50KB
  tool result.
* :class:`LineageReplay` — the response payload for
  ``GET /sessions/{session_id}/messages/{message_id}/lineage``. Returns
  ``None`` (HTTP 404 ``lineage.not_compressed``) when the target
  message has no ``original_turns_ref`` — i.e. the user asked to expand
  a row that was never a compaction summary.
* :class:`LineageSummary` — one row in the per-session summaries feed
  used by the chat trace tab badge ("compressed N original turns").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas._base import ORMModel


class LineageNode(ORMModel):
    message_id: uuid.UUID
    role: str
    text_excerpt: str = ""
    created_at: datetime
    is_compressed_summary: bool = False
    is_original_turn: bool = False


class LineageReplay(ORMModel):
    summary_message_id: uuid.UUID
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    original_turn_count: int = Field(ge=0)
    original_turns: list[LineageNode] = Field(default_factory=list)
    compaction_strategy: str
    compressed_at: datetime


class LineageSummary(ORMModel):
    summary_message_id: uuid.UUID
    role: str
    turn_count: int = Field(ge=0)
    compaction_strategy: str
    compressed_at: datetime
    summary_excerpt: str = ""
