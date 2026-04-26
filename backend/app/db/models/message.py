"""Message — one row per LLM turn part (user / assistant / tool_call / tool_result / thinking)."""

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
