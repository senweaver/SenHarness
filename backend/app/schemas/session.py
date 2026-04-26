"""Session + Message DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.message import MessageRole
from app.db.models.session import SessionKind, SessionState
from app.schemas._base import ORMModel, Timestamped


# ─── Session ──────────────────────────────────────────────
class SessionCreate(ORMModel):
    kind: SessionKind = SessionKind.P2P
    subject_id: uuid.UUID | None = None
    title: str | None = None


class SessionUpdate(ORMModel):
    title: str | None = None
    state: SessionState | None = None


class SessionRead(Timestamped):
    workspace_id: uuid.UUID
    kind: SessionKind
    subject_id: uuid.UUID | None
    channel_id: uuid.UUID | None
    owner_identity_id: uuid.UUID | None
    title: str | None
    state: SessionState
    summary_md: str | None
    last_message_at: datetime | None
    message_count: int
    metadata_json: dict


# ─── Message ──────────────────────────────────────────────
class MessageCreate(ORMModel):
    role: MessageRole = MessageRole.USER
    content_json: dict = Field(default_factory=dict)
    attachments_json: list = Field(default_factory=list)


class MessageRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    author_identity_id: uuid.UUID | None
    author_agent_id: uuid.UUID | None
    content_json: dict
    tool_call_json: dict | None
    tool_result_json: dict | None
    thinking_json: dict | None
    attachments_json: list
    token_usage_json: dict
    metadata_json: dict
