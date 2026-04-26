"""Session + Message service."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.db.models.message import Message, MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.repositories.session import MessageRepository, SessionRepository


async def create_session(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    kind: SessionKind = SessionKind.P2P,
    subject_id: uuid.UUID | None = None,
    title: str | None = None,
) -> SessionModel:
    return await SessionRepository(session).create(
        workspace_id=workspace_id,
        owner_identity_id=owner_identity_id,
        kind=kind,
        subject_id=subject_id,
        title=title,
    )


async def get_session_or_404(
    session: AsyncSession, session_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> SessionModel:
    s = await SessionRepository(session).get(session_id)
    if s is None or s.workspace_id != workspace_id:
        raise NotFound("session_not_found", code="session.not_found")
    return s


async def append_message(
    db: AsyncSession,
    *,
    session_obj: SessionModel,
    role: MessageRole,
    content_json: dict,
    author_identity_id: uuid.UUID | None = None,
    author_agent_id: uuid.UUID | None = None,
    attachments_json: list | None = None,
    tool_call_json: dict | None = None,
    tool_result_json: dict | None = None,
    thinking_json: dict | None = None,
    token_usage_json: dict | None = None,
) -> Message:
    msg = await MessageRepository(db).create(
        workspace_id=session_obj.workspace_id,
        session_id=session_obj.id,
        role=role,
        author_identity_id=author_identity_id,
        author_agent_id=author_agent_id,
        content_json=content_json,
        attachments_json=attachments_json or [],
        tool_call_json=tool_call_json,
        tool_result_json=tool_result_json,
        thinking_json=thinking_json,
        token_usage_json=token_usage_json or {},
    )
    session_obj.last_message_at = utcnow_naive()
    session_obj.message_count = (session_obj.message_count or 0) + 1
    await db.flush([session_obj])
    return msg
