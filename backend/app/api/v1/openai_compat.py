"""OpenAI-compatible endpoints mapped to SenHarness agents."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.kernels import protocol_kernel
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.session import SessionKind
from app.repositories.agent import AgentRepository
from app.repositories.session import SessionRepository
from app.services import agent_runner as runner
from app.services import audit as audit_svc
from app.services import served_model as served_svc
from app.services import workspace as ws_svc
from app.services.protocol_adapter import (
    NormalizedMessageRequest,
    anthropic_messages_to_normalized,
    estimate_tokens_for_normalized,
    normalized_to_anthropic_response,
    normalized_to_openai_responses,
    openai_responses_to_normalized,
    stream_anthropic_messages,
    stream_openai_responses,
)

log = logging.getLogger(__name__)

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


@router.get(
    "/models",
    dependencies=[Depends(rate_limit("openai_list_models", limit=60, period_seconds=60))],
)
async def list_models(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict[str, Any]:
    """OpenAI-compatible model listing — served names only (M2.5.7).

    Clients see the union of:

    * Stable per-agent ``served_model_name`` values declared on the
      workspace's agents.
    * Keys of ``workspace.home_config_json["providers"]["served_alias_map"]``.

    Swapping the upstream provider behind an alias does NOT change
    this listing — that's the whole point of the two-model-id pattern.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    entries = await served_svc.list_served_models_for_workspace(db, workspace_id=ws_id)
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": entry.served_name,
                "object": "model",
                "owned_by": "senharness",
                "created": now,
            }
            for entry in entries
        ],
    }


@router.post(
    "/chat/completions",
    dependencies=[Depends(rate_limit("openai_chat_completions", limit=60, period_seconds=60))],
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


# ─── M3.8 — Anthropic Messages + OpenAI Responses gateway ─────────
async def _audit_protocol_invocation(
    db: DBSession,
    *,
    action: str,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    normalized: NormalizedMessageRequest | None,
    extra: dict[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {}
    if normalized is not None:
        metadata.update(
            {
                "model": normalized.model,
                "stream": normalized.stream,
                "messages": len(normalized.messages),
                "tools": len(normalized.tools),
                "max_tokens": normalized.max_tokens,
            }
        )
    if extra:
        metadata.update(extra)
    await audit_svc.record(
        db,
        action=action,
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="protocol",
        resource_id=None,
        summary=action,
        metadata=metadata,
    )
    await db.commit()


async def _run_passthrough_one_shot(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    normalized: NormalizedMessageRequest,
) -> dict[str, Any]:
    try:
        return await protocol_kernel.run_kernel_one_shot(
            normalized,
            workspace_id=workspace_id,
            identity_id=identity_id,
        )
    except protocol_kernel.ProtocolKernelError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "protocol.no_provider", "message": str(exc)},
        ) from exc


async def _stream_with_kernel(
    encoder: Any,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    normalized: NormalizedMessageRequest,
) -> AsyncIterator[bytes]:
    try:
        kernel_stream = protocol_kernel.run_kernel_stream(
            normalized,
            workspace_id=workspace_id,
            identity_id=identity_id,
        )
    except protocol_kernel.ProtocolKernelError as exc:  # pragma: no cover
        err_message = str(exc)

        async def _error_only() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "error", "message": err_message}

        kernel_stream = _error_only()

    async for chunk in encoder(kernel_stream, request=normalized):
        yield chunk


@router.post(
    "/messages",
    dependencies=[Depends(rate_limit("anthropic_messages", limit=60, period_seconds=60))],
)
async def anthropic_messages_endpoint(
    body: dict,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> Any:
    """Anthropic-compatible ``POST /v1/messages`` (provider passthrough).

    Translates the body into the gateway's normalized shape, drives
    one upstream call via :mod:`app.agents.kernels.protocol_kernel`,
    and re-encodes the result into either the Anthropic message JSON
    (non-streaming) or Anthropic SSE (streaming).
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)

    try:
        normalized = anthropic_messages_to_normalized(body)
    except ValueError as exc:
        await audit_svc.record(
            db,
            action="protocol.translation_failed",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="protocol",
            summary="anthropic_messages translation failed",
            metadata={"error": str(exc), "endpoint": "anthropic_messages"},
        )
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={"code": "protocol.invalid_body", "message": str(exc)},
        ) from exc

    await _audit_protocol_invocation(
        db,
        action="protocol.anthropic_messages.invoked",
        workspace_id=ws_id,
        identity_id=identity_id,
        normalized=normalized,
    )

    if normalized.stream:

        async def _gen() -> AsyncIterator[bytes]:
            async for chunk in _stream_with_kernel(
                stream_anthropic_messages,
                workspace_id=ws_id,
                identity_id=identity_id,
                normalized=normalized,
            ):
                yield chunk

        return StreamingResponse(_gen(), media_type="text/event-stream")

    internal = await _run_passthrough_one_shot(
        workspace_id=ws_id,
        identity_id=identity_id,
        normalized=normalized,
    )
    return normalized_to_anthropic_response(internal, request=normalized)


@router.post(
    "/messages/count_tokens",
    dependencies=[Depends(rate_limit("anthropic_count_tokens", limit=120, period_seconds=60))],
)
async def anthropic_count_tokens(
    body: dict,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict[str, Any]:
    """Anthropic-compatible token counting helper.

    Uses the ``len(text) // 4`` heuristic that M2.5.9 cache sizing
    already relies on. Not a substitute for the upstream tokenizer
    but accurate enough for billing pre-flight and prompt sizing.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)

    try:
        normalized = anthropic_messages_to_normalized(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "protocol.invalid_body", "message": str(exc)},
        ) from exc

    estimated = estimate_tokens_for_normalized(normalized)

    await _audit_protocol_invocation(
        db,
        action="protocol.count_tokens.invoked",
        workspace_id=ws_id,
        identity_id=identity_id,
        normalized=normalized,
        extra={"estimated_input_tokens": estimated},
    )

    return {"input_tokens": estimated}


@router.post(
    "/responses",
    dependencies=[Depends(rate_limit("openai_responses", limit=60, period_seconds=60))],
)
async def openai_responses_endpoint(
    body: dict,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> Any:
    """OpenAI-compatible ``POST /v1/responses`` (provider passthrough).

    Replaces the M2 stub that only echoed the last user turn. Now
    drives the upstream model directly with full tool / vision / file
    fidelity and streams via the Responses SSE event sequence.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)

    try:
        normalized = openai_responses_to_normalized(body)
    except ValueError as exc:
        await audit_svc.record(
            db,
            action="protocol.translation_failed",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="protocol",
            summary="openai_responses translation failed",
            metadata={"error": str(exc), "endpoint": "openai_responses"},
        )
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={"code": "protocol.invalid_body", "message": str(exc)},
        ) from exc

    await _audit_protocol_invocation(
        db,
        action="protocol.openai_responses.invoked",
        workspace_id=ws_id,
        identity_id=identity_id,
        normalized=normalized,
    )

    if normalized.stream:

        async def _gen() -> AsyncIterator[bytes]:
            async for chunk in _stream_with_kernel(
                stream_openai_responses,
                workspace_id=ws_id,
                identity_id=identity_id,
                normalized=normalized,
            ):
                yield chunk

        return StreamingResponse(_gen(), media_type="text/event-stream")

    internal = await _run_passthrough_one_shot(
        workspace_id=ws_id,
        identity_id=identity_id,
        normalized=normalized,
    )
    return normalized_to_openai_responses(internal, request=normalized)
