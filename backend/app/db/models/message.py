"""Message — one row per LLM turn part (user / assistant / tool_call / tool_result / thinking).

M4.3 added two columns so the chat trace UI can expand a compressed
summary back into the original turns it folded:

* ``compressed_into_summary_id`` — self-FK pointing at the summary
  row that absorbed this turn. Set on each *original* row once the
  compaction layer (sliding-window / manual / evolver) writes a
  summary message. ``ON DELETE SET NULL`` keeps the original turn
  intact when the summary is purged so the lineage chain degrades
  gracefully rather than cascading.
* ``original_turns_ref`` — JSONB metadata stamped on the *summary*
  row only. Records which turns it summarised, when, and which
  strategy emitted it. Read-only side info — the runtime never
  feeds this dict to the LLM, so a compaction event does NOT break
  the M0.7 cache prefix invariant on subsequent turns.

Schema for ``original_turns_ref``::

    {
      "turn_message_ids": [str(msg_id), ...],
      "turn_count": int,
      "compressed_at": "ISO timestamp",
      "compaction_strategy": "sliding_window" | "manual" | "evolver",
    }
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    APPROVAL = "approval"
    HANDOFF = "handoff"


class Message(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(String(32), nullable=False, index=True)

    author_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Normalized multi-part content (text / image / file refs).
    content_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    tool_call_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    thinking_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attachments_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    token_usage_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    compressed_into_summary_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_turns_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


COMPACTION_STRATEGIES: frozenset[str] = frozenset(
    {"sliding_window", "manual", "evolver"}
)
LINEAGE_TEXT_EXCERPT_MAX_CHARS: int = 200
