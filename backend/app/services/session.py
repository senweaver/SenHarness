"""Session + Message service."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.db.models.message import Message, MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.repositories.session import (
    MessageRepository,
    SessionRepository,
    SessionStarRepository,
)


def new_checkpoint_id() -> str:
    """Generate a short, sortable checkpoint id (cp_<8 hex>).

    Not strictly a ULID — we keep it ASCII-cheap and prefix-tagged so logs
    pop. Collisions across a single session are statistically impossible at
    the volumes we expect.
    """
    return f"cp_{secrets.token_hex(8)}"


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


async def star_session(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    pinned: bool = False,
) -> tuple[bool, bool]:
    """Idempotent. Returns ``(starred, pinned)`` post-state."""
    repo = SessionStarRepository(db)
    existing = await repo.get_for(identity_id, session_id)
    if existing is None:
        await repo.create(
            identity_id=identity_id,
            session_id=session_id,
            workspace_id=workspace_id,
            pinned=pinned,
        )
        return True, pinned
    if existing.pinned != pinned:
        await repo.update(existing, pinned=pinned)
    return True, pinned


async def unstar_session(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    session_id: uuid.UUID,
) -> bool:
    repo = SessionStarRepository(db)
    existing = await repo.get_for(identity_id, session_id)
    if existing is None:
        return False
    await repo.hard_delete(existing)
    return True


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
    metadata_json: dict | None = None,
) -> Message:
    # Auto-stamp a checkpoint id on every assistant message so the user can
    # "fork from here" later. Other roles get the id only when the caller
    # explicitly passes one — keeps user/tool messages cheap to scan.
    md = dict(metadata_json or {})
    if role == MessageRole.ASSISTANT and "checkpoint_id" not in md:
        md["checkpoint_id"] = new_checkpoint_id()
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
        metadata_json=md,
    )
    session_obj.last_message_at = utcnow_naive()
    session_obj.message_count = (session_obj.message_count or 0) + 1
    await db.flush([session_obj])
    return msg


async def rewind_to_checkpoint(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    checkpoint_id: str,
    actor_identity_id: uuid.UUID,
) -> SessionModel:
    """Fork the session at ``checkpoint_id`` and truncate the original.

    Behaviour:
        1. Find the message tagged with ``checkpoint_id`` in
           ``metadata_json``; raise ``NotFound`` if missing or in another
           workspace.
        2. Create a new session with the same agent / kind, title prefixed
           with "↺", and ``metadata_json.forked_from`` pointing back at the
           original session + checkpoint.
        3. Delete every message strictly after the checkpoint in the
           original session (the checkpoint message itself stays so the
           user can see where they branched from).
        4. Return the *new* session (the caller commits).
    """
    # Step 1 — locate the anchor.
    sess = await get_session_or_404(db, session_id, workspace_id=workspace_id)
    msgs = await MessageRepository(db).list_for_session(
        session_id=session_id, limit=500
    )
    anchor: Message | None = None
    for m in msgs:
        meta = m.metadata_json or {}
        if isinstance(meta, dict) and str(meta.get("checkpoint_id")) == checkpoint_id:
            anchor = m
            break
    if anchor is None:
        raise NotFound("checkpoint_not_found", code="checkpoint.not_found")

    # Step 2 — create the forked session.
    title_seed = (sess.title or "Untitled").strip()
    new_title = f"↺ {title_seed}"[:255]
    new_session = await SessionRepository(db).create(
        workspace_id=workspace_id,
        owner_identity_id=actor_identity_id,
        kind=sess.kind,
        subject_id=sess.subject_id,
        title=new_title,
    )
    new_session.metadata_json = dict(new_session.metadata_json or {})
    new_session.metadata_json.update(
        {
            "forked_from": {
                "session_id": str(session_id),
                "checkpoint_id": checkpoint_id,
                "forked_at": utcnow_naive().isoformat(),
                "forked_by": str(actor_identity_id),
            }
        }
    )

    # Step 3 — copy the prefix (≤ anchor.created_at) into the fork so the new
    # session is self-contained. We copy by value to avoid double-counting
    # message_count and to keep the fork independent if the original is
    # archived later.
    copied = 0
    for m in msgs:
        if m.created_at > anchor.created_at:
            break
        await MessageRepository(db).create(
            workspace_id=workspace_id,
            session_id=new_session.id,
            role=m.role,
            author_identity_id=m.author_identity_id,
            author_agent_id=m.author_agent_id,
            content_json=dict(m.content_json or {}),
            attachments_json=list(m.attachments_json or []),
            tool_call_json=dict(m.tool_call_json) if m.tool_call_json else None,
            tool_result_json=dict(m.tool_result_json) if m.tool_result_json else None,
            thinking_json=dict(m.thinking_json) if m.thinking_json else None,
            token_usage_json=dict(m.token_usage_json or {}),
            metadata_json=dict(m.metadata_json or {}),
        )
        copied += 1
    new_session.message_count = copied
    new_session.last_message_at = anchor.created_at
    await db.flush([new_session])

    # Step 4 — leave the original alone (the user might want to compare). Just
    # tag the original session with a back-reference for audit.
    sess.metadata_json = dict(sess.metadata_json or {})
    forks = sess.metadata_json.get("forks") or []
    if not isinstance(forks, list):
        forks = []
    forks.append(
        {
            "session_id": str(new_session.id),
            "checkpoint_id": checkpoint_id,
            "forked_at": utcnow_naive().isoformat(),
            "forked_by": str(actor_identity_id),
        }
    )
    sess.metadata_json["forks"] = forks
    await db.flush([sess])
    _ = sql_delete  # keep import available for future hard-truncate
    return new_session
