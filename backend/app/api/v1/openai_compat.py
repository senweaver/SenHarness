"""OpenAI-compatible endpoints mapped to SenHarness agents."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.session import SessionKind
from app.repositories.agent import AgentRepository
from app.repositories.session import SessionRepository
from app.services import agent_runner as runner
from app.services import workspace as ws_svc

router = APIRouter(prefix="/openai/v1", tags=["openai_compat"])


class _ChatMessage(BaseModel):
    role: str
    content: str | list[dict] | None = None


class ChatCompletionsIn(BaseModel):
    model: str | None = None
    messages: list[_ChatMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    metadata: dict = Field(default_factory=dict)


class ResponsesIn(BaseModel):
    model: str | None = None
    input: str | list[dict] | list[_ChatMessage]
    stream: bool = False
    max_output_tokens: int | None = None
    metadata: dict = Field(default_factory=dict)


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _extract_last_user_text(messages: list[_ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        content = msg.content
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
            merged = "\n".join(parts).strip()
            if merged:
                return merged
    return ""


def _extract_input_text(input_value: str | list[dict] | list[_ChatMessage]) -> str:
    if isinstance(input_value, str):
        return input_value.strip()
    messages: list[_ChatMessage] = []
    for item in input_value:
        if isinstance(item, _ChatMessage):
            messages.append(item)
        elif isinstance(item, dict):
            try:
                messages.append(_ChatMessage.model_validate(item))
            except Exception:
                continue
    return _extract_last_user_text(messages)


def _usage_blob(payload: dict) -> dict[str, int]:
    tokens = (payload.get("tokens") or {}) if isinstance(payload, dict) else {}
    prompt = int(tokens.get("input") or 0)
    completion = int(tokens.get("output") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


async def _resolve_default_agent_id(db: DBSession, *, workspace_id: uuid.UUID) -> uuid.UUID:
    agent = await AgentRepository(db).get_default_for_workspace(workspace_id=workspace_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="no_agent_in_workspace")
    return agent.id


async def _run_default_agent(
    db: DBSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    user_text: str,
):
    if not user_text.strip():
        raise HTTPException(status_code=400, detail="empty_user_message")
    agent_id = await _resolve_default_agent_id(db, workspace_id=workspace_id)
    session_obj = await SessionRepository(db).create(
        workspace_id=workspace_id,
        kind=SessionKind.P2P,
        subject_id=agent_id,
        owner_identity_id=identity_id,
        title="OpenAI compat session",
        metadata_json={"source": "openai_compat"},
    )
    result = await runner.run_agent_one_shot(
        db,
        workspace_id=workspace_id,
        agent_id=agent_id,
        session_id=session_obj.id,
        identity_id=identity_id,
        user_text=user_text,
    )
    await db.commit()
    return result


@router.post(
    "/chat/completions",
    dependencies=[
        Depends(rate_limit("openai_chat_completions", limit=60, period_seconds=60))
    ],
)
async def chat_completions(
    body: ChatCompletionsIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(status_code=400, detail="stream_not_supported_yet")
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    user_text = _extract_last_user_text(body.messages)
    result = await _run_default_agent(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        user_text=user_text,
    )
    usage = _usage_blob(result.usage_payload)
    model_name = body.model or "senharness-default-agent"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.final_text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


@router.post(
    "/responses",
    dependencies=[
        Depends(rate_limit("openai_responses", limit=60, period_seconds=60))
    ],
)
async def responses(
    body: ResponsesIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(status_code=400, detail="stream_not_supported_yet")
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    user_text = _extract_input_text(body.input)
    result = await _run_default_agent(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        user_text=user_text,
    )
    usage = _usage_blob(result.usage_payload)
    model_name = body.model or "senharness-default-agent"
    return {
        "id": f"resp-{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model_name,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": result.final_text}],
            }
        ],
        "output_text": result.final_text,
        "usage": usage,
    }
