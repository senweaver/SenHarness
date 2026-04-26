"""Non-interactive agent runner — used by Channels (IM) and Flows.

The WebSocket path in ``app.api.v1.sessions`` streams RunEvents to a browser.
Channels and Flows don't have a browser — they want the full final answer +
some stats. This module drives the same backend but collects the result into
a simple ``AgentResult``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.registry import get_backend
from app.db.models.agent import BackendKind
from app.db.models.message import MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.repositories.agent import AgentRepository
from app.repositories.session import MessageRepository, SessionRepository
from app.services import session as sess_svc

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    final_text: str = ""
    tool_events: list[dict] = field(default_factory=list)
    usage_payload: dict = field(default_factory=dict)
    error: str | None = None
    session_id: uuid.UUID | None = None


async def run_agent_one_shot(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    user_text: str,
    iteration_budget: int = 8,
) -> AgentResult:
    """Run one turn for the given (session, agent) and persist the assistant
    message with the full final_text + tool events + token usage.

    Returns the collected output. The caller is responsible for commit().
    """
    result = AgentResult(session_id=session_id)

    agent = await AgentRepository(db).get(agent_id)
    if agent is None:
        result.error = "agent_not_found"
        return result

    session_obj = await SessionRepository(db).get(session_id)
    if session_obj is None:
        result.error = "session_not_found"
        return result

    # Append the user message first so the history processor sees it.
    await sess_svc.append_message(
        db,
        session_obj=session_obj,
        role=MessageRole.USER,
        content_json={"text": user_text},
        author_identity_id=identity_id,
    )
    await db.flush()

    # Build history from persisted rows (last ~40 including the new user row).
    msg_rows = await MessageRepository(db).list_recent(
        session_id=session_id, limit=40
    )
    history: list[dict] = []
    for m in msg_rows:
        if m.role == MessageRole.USER:
            history.append({"role": "user", "text": (m.content_json or {}).get("text", "")})
        elif m.role == MessageRole.ASSISTANT:
            history.append({"role": "assistant", "text": (m.content_json or {}).get("text", "")})

    backend = get_backend(agent.backend_kind)
    if backend is None:
        # Fall back to the bundled native runtime so Kind mismatch still runs.
        backend = get_backend(BackendKind.NATIVE)
    if backend is None:
        result.error = "no_backend"
        return result

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_id=agent.id,
        session_id=session_id,
        identity_id=identity_id or uuid.UUID(int=0),
        user_text=user_text,
        message_history=history[:-1],  # exclude the just-appended user turn
        toolbox=[],
        skills=[],
        policy={
            "autonomy_level": agent.autonomy_level,
            "backend_adapter_id": (
                str(agent.backend_adapter_id)
                if getattr(agent, "backend_adapter_id", None)
                else None
            ),
            "code_mode": agent.metadata_json.get("code_mode"),
            "context": agent.metadata_json.get("context") or {},
            "skills": agent.metadata_json.get("skills"),
            "todos": agent.metadata_json.get("todos"),
            "sandbox": agent.metadata_json.get("sandbox"),
            # Channels / Flows can't reasonably do HITL — force approvals off.
            "approvals": False,
            "persona_md": agent.persona_md,
            "workspace_id": str(workspace_id),
            "session_id": str(session_id),
        },
        iteration_budget=iteration_budget,
    )

    full_text_parts: list[str] = []
    try:
        async for ev in backend.run(req):
            if ev.kind == RunEventKind.DELTA:
                full_text_parts.append(ev.data.get("text", ""))
            elif ev.kind in (RunEventKind.TOOL_CALL, RunEventKind.TOOL_RESULT):
                result.tool_events.append(ev.data)
            elif ev.kind == RunEventKind.USAGE:
                result.usage_payload = ev.data
            elif ev.kind == RunEventKind.ERROR:
                result.error = str(ev.data.get("message") or ev.data)
    except Exception as e:  # pragma: no cover
        log.exception("run_agent_one_shot failed")
        result.error = str(e)

    result.final_text = "".join(full_text_parts)

    # Persist assistant message so /sessions/{id}/messages reflects the turn.
    tokens = (result.usage_payload.get("tokens") or {})
    await sess_svc.append_message(
        db,
        session_obj=session_obj,
        role=MessageRole.ASSISTANT,
        content_json={"text": result.final_text},
        author_agent_id=agent.id,
        tool_call_json=(
            {"events": result.tool_events} if result.tool_events else None
        ),
        token_usage_json=_usage_blob(result.usage_payload, tokens),
    )

    _ = SessionKind  # keep import
    return result


def _usage_blob(usage: dict, tokens: dict) -> dict:
    inp = int(tokens.get("input") or 0)
    out = int(tokens.get("output") or 0)
    cost = float(usage.get("cost") or 0.0)
    if inp == 0 and out == 0 and cost == 0.0:
        return {}
    return {
        "input": inp,
        "output": out,
        "cost": cost,
        "cost_currency": usage.get("cost_currency") or "USD",
        "cost_matched_model": usage.get("cost_matched_model"),
        "latency_ms": int(usage.get("latency_ms") or 0),
        "provider": usage.get("provider"),
        "model": usage.get("model"),
    }


async def ensure_channel_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    channel_id: uuid.UUID,
    thread_key: str,
    subject_id: uuid.UUID,
    title_hint: str | None = None,
) -> SessionModel:
    """Get-or-create a Session for an IM thread — keyed by the channel +
    external thread key.

    Subsequent messages from the same IM thread land on the same Session so
    the Agent sees prior turns.
    """
    # Find an existing session for this channel+thread.
    existing = await SessionRepository(db).find_channel_session(
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_key=thread_key,
    )
    if existing is not None:
        return existing

    new_session = await SessionRepository(db).create(
        workspace_id=workspace_id,
        kind=SessionKind.CHANNEL,
        subject_id=subject_id,
        channel_id=channel_id,
        title=title_hint or thread_key,
        metadata_json={"thread_key": thread_key},
    )
    return new_session
