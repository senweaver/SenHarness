"""Trace replay — chronological reconstruction of a session's agent runs.

``GET /traces/sessions/{session_id}``
    Returns every message in a session (user / assistant / tool_call /
    tool_result / thinking) along with usage + eval metadata so the
    frontend can render a timeline for debugging.

``GET /traces/sessions/{session_id}/runs/{run_id}``
    Narrows the trace to a single run (via ``metadata_json.run_id``), useful
    when a session has hundreds of turns.

Auth: the caller must be a workspace member; non-owners only see their own
sessions. The payload deliberately excludes any message contents that live
outside the active workspace — scoping happens at the repository level.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import asc, select

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.message import Message
from app.db.models.session import Session as SessionModel
from app.services import workspace as ws_svc

router = APIRouter(prefix="/traces", tags=["traces"])


async def _load_session(
    db: DBSession,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> SessionModel:
    stmt = select(SessionModel).where(SessionModel.id == session_id)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None or session.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="session_not_found")

    member = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    from app.db.models.role import BuiltinRole

    is_admin = member.role in {BuiltinRole.OWNER.value, BuiltinRole.ADMIN.value}
    if not is_admin and session.owner_identity_id != identity_id:
        raise Unauthorized(
            "not_trace_owner",
            code="traces.not_owner",
        )
    return session


def _message_to_event(msg: Message) -> dict[str, Any]:
    """Project a Message row to the shape the trace-replay UI expects."""
    payload: dict[str, Any] = {
        "message_id": str(msg.id),
        "role": msg.role,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "content": msg.content_json or {},
        "attachments": msg.attachments_json or [],
        "token_usage": msg.token_usage_json or {},
        "metadata": msg.metadata_json or {},
    }
    if msg.tool_call_json:
        payload["tool_call"] = msg.tool_call_json
    if msg.tool_result_json:
        payload["tool_result"] = msg.tool_result_json
    if msg.thinking_json:
        payload["thinking"] = msg.thinking_json
    return payload


@router.get(
    "/sessions/{session_id}",
    summary="Replay a session's full trace",
)
async def replay_session(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")

    session = await _load_session(
        db,
        session_id=session_id,
        workspace_id=workspace_id,
        identity_id=identity_id,
    )

    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(asc(Message.created_at), asc(Message.id))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    events = [_message_to_event(m) for m in rows]

    # Aggregate usage across assistant messages in the returned window.
    total_input = total_output = 0
    total_cost = 0.0
    for m in rows:
        if m.role != "assistant":
            continue
        usage = m.token_usage_json or {}
        total_input += int((usage.get("tokens") or {}).get("input") or 0)
        total_output += int((usage.get("tokens") or {}).get("output") or 0)
        total_cost += float(usage.get("cost") or 0.0)

    # Evaluator verdicts live under ``metadata_json.eval`` (see evaluator.py).
    verdicts = [
        (m.metadata_json or {}).get("eval")
        for m in rows
        if (m.metadata_json or {}).get("eval")
    ]

    return {
        "session_id": str(session_id),
        "title": session.title,
        "agent_id": str(session.agent_id) if getattr(session, "agent_id", None) else None,
        "event_count": len(events),
        "events": events,
        "summary": {
            "tokens": {"input": total_input, "output": total_output},
            "cost_usd": round(total_cost, 6),
            "eval_verdicts": verdicts,
        },
    }


@router.get(
    "/sessions/{session_id}/runs/{run_id}",
    summary="Replay a single run within a session",
)
async def replay_run(
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict[str, Any]:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")

    await _load_session(
        db,
        session_id=session_id,
        workspace_id=workspace_id,
        identity_id=identity_id,
    )

    # ``metadata_json.run_id`` is set by the sessions WS handler when
    # persisting each assistant / tool / usage row. We filter with a JSONB
    # containment check so the query stays indexable on SQLA side.
    stmt = (
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.metadata_json["run_id"].astext == str(run_id),
        )
        .order_by(asc(Message.created_at), asc(Message.id))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "session_id": str(session_id),
        "run_id": str(run_id),
        "event_count": len(rows),
        "events": [_message_to_event(m) for m in rows],
    }
