"""Protocol passthrough kernel — minimal upstream model driver.

M3.8 — drives a single one-shot turn against the upstream provider
without any SenHarness skill / memory / shields injection. Used by
the Anthropic Messages and OpenAI Responses gateway endpoints; not
used by the regular agent run path (the WS / channel pipeline still
goes through :mod:`app.agents.kernels.native.runner`).

Why bypass the harness layers
-----------------------------

External agent frameworks (Claude Code, Codex, etc.) maintain their
own context, memory, tool catalogue, and approval flow. Re-injecting
SenHarness skills or memory fragments here would either contaminate
the framework's prompt budget or break tool_use fidelity. The
gateway is a thin protocol shim — let the framework own the harness.

What still applies
------------------

* **Two-Model-ID resolution** (M2.5.7). ``request.model`` is treated
  as a served name; the workspace alias map redirects to the real
  upstream when configured. This keeps provider-side prompt cache
  prefixes stable across provider swaps.
* **Vault-resolved API key**. We never read provider keys from the
  process env — only via the workspace's enabled provider rows.
* **Audit row per call**. The route writes ``protocol.<X>.invoked``;
  the kernel surfaces ``stop_reason`` / ``usage`` for the row.

Streaming contract
------------------

:func:`run_kernel_stream` yields plain dict frames so the protocol
adapter can re-encode them as Anthropic SSE or OpenAI Responses SSE
without leaking any pydantic-ai internals into the route layer.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
    parse_override,
    resolve_for_workspace,
)
from app.db.session import get_session_factory
from app.services.protocol_adapter import NormalizedMessageRequest
from app.services.served_model import resolve_served_model

log = logging.getLogger(__name__)


# ─── Public dataclass-ish dict shape ─────────────────────────
# run_kernel_one_shot returns:
#   {
#     "output_text": str,
#     "tool_uses": [{"id": str, "name": str, "input": dict}, ...],
#     "usage": {"input_tokens": int, "output_tokens": int},
#     "stop_reason": "end_turn" | "tool_use" | "max_tokens" | "stop_sequence",
#     "model": str,           # served name
#     "upstream_model": str,  # real upstream model id
#     "provider_kind": str,
#   }
#
# run_kernel_stream yields dicts of the shape documented in
# protocol_adapter.stream_anthropic_messages / stream_openai_responses.


class ProtocolKernelError(Exception):
    """Raised when no upstream model can be resolved for the workspace."""


# ─── Provider resolution ─────────────────────────────────────
async def _resolve_upstream(
    *, workspace_id: uuid.UUID, requested_model: str
) -> tuple[ResolvedModel, str]:
    """Return ``(ResolvedModel, served_name)`` or raise ProtocolKernelError.

    Resolution mirrors the runner's M2.5.7 logic but with no agent row:

    1. Check the workspace alias map for ``requested_model``.
    2. If present → use the mapped upstream as the override.
    3. If not → treat ``requested_model`` as a literal
       ``provider:model`` override (best-effort) and fall back to
       the workspace's first enabled provider.
    """
    factory = get_session_factory()
    async with factory() as session:
        envelope = await resolve_served_model(
            session,
            workspace_id=workspace_id,
            agent=None,
            fallback_upstream=requested_model or None,
        )

    served_name = envelope.served_name or requested_model

    override = envelope.upstream or requested_model
    parsed = parse_override(override) if override else None

    if parsed is not None:
        if parsed.api_key is None:
            db_resolved = await resolve_for_workspace(
                workspace_id=workspace_id, kind=parsed.provider_kind
            )
            if db_resolved is not None:
                parsed.api_key = db_resolved.api_key
                parsed.base_url = parsed.base_url or db_resolved.base_url
        if parsed.api_key:
            return parsed, served_name

    db_resolved = await resolve_for_workspace(workspace_id=workspace_id)
    if db_resolved is None:
        raise ProtocolKernelError("no_provider_configured")

    if requested_model and ":" not in requested_model:
        # Caller asked for a bare model name — keep their pick on the
        # served line but route via the workspace's default provider.
        db_resolved = ResolvedModel(
            provider_kind=db_resolved.provider_kind,
            model_name=requested_model,
            api_key=db_resolved.api_key,
            base_url=db_resolved.base_url,
            source=db_resolved.source,
        )
    return db_resolved, served_name or db_resolved.model_name


# ─── Normalized → pydantic-ai messages ──────────────────────
def _build_user_content_parts(parts: list[dict[str, Any]]) -> list[Any]:
    """Convert internal parts → pydantic-ai ``UserContent`` sequence.

    Image / file parts use BinaryContent (base64) or ImageUrl /
    DocumentUrl (URL) so the upstream provider's pydantic-ai
    integration handles vendor-specific encoding (Anthropic source
    block, OpenAI image_url, Gemini Part etc.).
    """
    import base64

    from pydantic_ai.messages import (
        BinaryContent,
        DocumentUrl,
        ImageUrl,
    )

    out: list[Any] = []
    for part in parts:
        kind = part.get("type")
        if kind == "text":
            text = str(part.get("text") or "")
            if text:
                out.append(text)
        elif kind == "image_data":
            try:
                raw = base64.b64decode(part.get("data") or "", validate=False)
            except Exception:
                raw = b""
            out.append(
                BinaryContent(data=raw, media_type=str(part.get("media_type") or "image/png"))
            )
        elif kind == "image_url":
            url = str(part.get("url") or "")
            if url:
                out.append(ImageUrl(url=url))
        elif kind == "file_data":
            try:
                raw = base64.b64decode(part.get("data") or "", validate=False)
            except Exception:
                raw = b""
            out.append(
                BinaryContent(
                    data=raw,
                    media_type=str(part.get("media_type") or "application/octet-stream"),
                )
            )
        elif kind == "file_url":
            url = str(part.get("url") or "")
            if url:
                out.append(DocumentUrl(url=url))
    return out


def _normalize_to_pydantic_ai_messages(req: NormalizedMessageRequest) -> list[Any]:
    """Project the gateway's normalized history onto ``list[ModelMessage]``.

    Tool_use blocks on assistant turns become ``ToolCallPart``;
    tool_result blocks on user turns become ``ToolReturnPart``.
    Multiple consecutive parts of the same role merge into one
    ``ModelRequest`` (user) or ``ModelResponse`` (assistant) so the
    provider sees a single turn, not a fragmented one.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    messages: list[Any] = []

    if req.system:
        messages.append(ModelRequest(parts=[SystemPromptPart(content=req.system)]))

    for entry in req.messages:
        role = entry.get("role")
        parts = entry.get("content") or []
        if role == "user":
            request_parts: list[Any] = []
            user_content_buffer: list[dict[str, Any]] = []

            def _flush_user_buffer() -> None:
                if not user_content_buffer:
                    return
                pieces = _build_user_content_parts(user_content_buffer)
                if not pieces:
                    user_content_buffer.clear()
                    return
                if len(pieces) == 1 and isinstance(pieces[0], str):
                    request_parts.append(UserPromptPart(content=pieces[0]))
                else:
                    request_parts.append(UserPromptPart(content=pieces))
                user_content_buffer.clear()

            for part in parts:
                if part.get("type") == "tool_result":
                    _flush_user_buffer()
                    inner_text_chunks: list[str] = []
                    for sub in part.get("content") or ():
                        if sub.get("type") == "text":
                            inner_text_chunks.append(str(sub.get("text") or ""))
                    payload: Any = "\n".join(inner_text_chunks).strip() or ""
                    request_parts.append(
                        ToolReturnPart(
                            tool_name=str(part.get("tool_name") or ""),
                            content=payload,
                            tool_call_id=str(part.get("tool_use_id") or ""),
                        )
                    )
                else:
                    user_content_buffer.append(part)
            _flush_user_buffer()
            if request_parts:
                messages.append(ModelRequest(parts=request_parts))
        elif role == "assistant":
            response_parts: list[Any] = []
            for part in parts:
                if part.get("type") == "text":
                    text = str(part.get("text") or "")
                    if text:
                        response_parts.append(TextPart(content=text))
                elif part.get("type") == "tool_use":
                    response_parts.append(
                        ToolCallPart(
                            tool_name=str(part.get("name") or ""),
                            args=part.get("input") or {},
                            tool_call_id=str(part.get("id") or ""),
                        )
                    )
            if response_parts:
                messages.append(ModelResponse(parts=response_parts))
    return messages


def _build_request_parameters(req: NormalizedMessageRequest) -> Any:
    """Build ModelRequestParameters with the protocol's tool catalogue."""
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.tools import ToolDefinition

    function_tools = [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters_json_schema=tool.parameters_schema,
        )
        for tool in req.tools
    ]
    return ModelRequestParameters(
        function_tools=function_tools,
        builtin_tools=[],
        output_mode="text",
        output_object=None,
        output_tools=[],
        prompted_output_template=None,
        allow_text_output=True,
        allow_image_output=False,
        instruction_parts=[],
    )


def _build_model_settings(req: NormalizedMessageRequest) -> Any:
    from pydantic_ai.settings import ModelSettings

    settings: dict[str, Any] = {"max_tokens": req.max_tokens}
    if req.temperature is not None:
        settings["temperature"] = req.temperature
    if req.top_p is not None:
        settings["top_p"] = req.top_p
    if req.stop_sequences:
        settings["stop_sequences"] = list(req.stop_sequences)
    return ModelSettings(**settings)


# ─── Non-streaming entry point ───────────────────────────────
async def run_kernel_one_shot(
    normalized: NormalizedMessageRequest,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Drive one upstream call and return the protocol-agnostic result.

    Contract:

    * Never raises on provider errors — surfaces them via the
      ``stop_reason="error"`` + ``error_message`` slot.
    * Raises :class:`ProtocolKernelError` when there is no usable
      provider in the workspace at all (route maps that to 503).
    """
    _ = identity_id  # reserved for per-identity rate / quota in M3.9+

    resolved, served_name = await _resolve_upstream(
        workspace_id=workspace_id, requested_model=normalized.model
    )

    model = build_pydantic_ai_model(resolved)
    if model is None:
        raise ProtocolKernelError(
            f"model_build_failed:{resolved.provider_kind}:{resolved.model_name}"
        )

    pydantic_messages = _normalize_to_pydantic_ai_messages(normalized)
    request_parameters = _build_request_parameters(normalized)
    model_settings = _build_model_settings(normalized)

    try:
        response = await model.request(pydantic_messages, model_settings, request_parameters)
    except Exception as exc:  # pragma: no cover - provider failures live here
        log.warning("protocol_kernel upstream request failed: %s", exc)
        return {
            "output_text": "",
            "tool_uses": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "stop_reason": "error",
            "error_message": str(exc),
            "model": served_name,
            "upstream_model": resolved.model_name,
            "provider_kind": resolved.provider_kind,
        }

    return _shape_response(
        response,
        served_name=served_name,
        upstream_model=resolved.model_name,
        provider_kind=resolved.provider_kind,
    )


def _shape_response(
    response: Any, *, served_name: str, upstream_model: str, provider_kind: str
) -> dict[str, Any]:
    from pydantic_ai.messages import TextPart, ToolCallPart

    text_chunks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    has_tool_use = False
    for part in getattr(response, "parts", []) or ():
        if isinstance(part, TextPart):
            text_chunks.append(part.content or "")
        elif isinstance(part, ToolCallPart):
            has_tool_use = True
            args = part.args
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            tool_uses.append(
                {
                    "id": part.tool_call_id or part.id or f"toolu_{uuid.uuid4().hex}",
                    "name": part.tool_name,
                    "input": args if isinstance(args, dict) else {"_value": args},
                }
            )

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0

    finish_reason = getattr(response, "finish_reason", None)
    stop_reason = _map_finish_reason(finish_reason, has_tool_use=has_tool_use)

    return {
        "output_text": "".join(text_chunks),
        "tool_uses": tool_uses,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "stop_reason": stop_reason,
        "model": served_name,
        "upstream_model": upstream_model,
        "provider_kind": provider_kind,
    }


def _map_finish_reason(raw: Any, *, has_tool_use: bool) -> str:
    """Map pydantic-ai's finish reason onto Anthropic ``stop_reason`` values."""
    if has_tool_use:
        return "tool_use"
    if raw is None:
        return "end_turn"
    name = getattr(raw, "value", None) or str(raw)
    name = str(name).lower()
    if name in ("stop", "end_turn", "completed"):
        return "end_turn"
    if name in ("length", "max_tokens"):
        return "max_tokens"
    if name in ("content_filter",):
        return "stop_sequence"
    if name in ("tool_calls", "tool_use", "function_call"):
        return "tool_use"
    return "end_turn"


# ─── Streaming entry point ──────────────────────────────────
async def run_kernel_stream(
    normalized: NormalizedMessageRequest,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream upstream output as protocol-agnostic dict frames.

    Frame types: ``start`` / ``text_delta`` / ``tool_use_start`` /
    ``tool_use_delta`` / ``tool_use_stop`` / ``stop`` / ``error``.
    See :mod:`app.services.protocol_adapter` for how each frame is
    re-encoded into Anthropic SSE or OpenAI Responses SSE.
    """
    _ = identity_id

    resolved, served_name = await _resolve_upstream(
        workspace_id=workspace_id, requested_model=normalized.model
    )

    model = build_pydantic_ai_model(resolved)
    if model is None:
        yield {
            "type": "error",
            "message": f"model_build_failed:{resolved.provider_kind}:{resolved.model_name}",
        }
        return

    pydantic_messages = _normalize_to_pydantic_ai_messages(normalized)
    request_parameters = _build_request_parameters(normalized)
    model_settings = _build_model_settings(normalized)

    yield {"type": "start", "model": served_name}

    has_tool_use = False
    open_tool_call_id: str | None = None
    open_tool_call_name: str | None = None
    final_response: Any | None = None

    try:
        from pydantic_ai.messages import (
            FinalResultEvent,
            PartDeltaEvent,
            PartStartEvent,
            TextPart,
            TextPartDelta,
            ToolCallPart,
            ToolCallPartDelta,
        )

        async with model.request_stream(
            pydantic_messages, model_settings, request_parameters
        ) as response_stream:
            async for event in response_stream:
                if isinstance(event, PartStartEvent):
                    part = event.part
                    if isinstance(part, ToolCallPart):
                        has_tool_use = True
                        if open_tool_call_id is not None:
                            yield {
                                "type": "tool_use_stop",
                                "id": open_tool_call_id,
                            }
                        open_tool_call_id = part.tool_call_id or f"toolu_{uuid.uuid4().hex}"
                        open_tool_call_name = part.tool_name
                        yield {
                            "type": "tool_use_start",
                            "id": open_tool_call_id,
                            "name": open_tool_call_name,
                        }
                        if part.args:
                            args_str = (
                                part.args
                                if isinstance(part.args, str)
                                else json.dumps(part.args, ensure_ascii=False)
                            )
                            if args_str:
                                yield {
                                    "type": "tool_use_delta",
                                    "id": open_tool_call_id,
                                    "input_json": args_str,
                                }
                    elif isinstance(part, TextPart):
                        if part.content:
                            yield {"type": "text_delta", "text": part.content}
                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta):
                        chunk = event.delta.content_delta or ""
                        if chunk:
                            yield {"type": "text_delta", "text": chunk}
                    elif isinstance(event.delta, ToolCallPartDelta):
                        has_tool_use = True
                        delta_id = event.delta.tool_call_id or open_tool_call_id
                        if delta_id is None:
                            continue
                        if event.delta.tool_name_delta and open_tool_call_id != delta_id:
                            if open_tool_call_id is not None:
                                yield {
                                    "type": "tool_use_stop",
                                    "id": open_tool_call_id,
                                }
                            open_tool_call_id = delta_id
                            open_tool_call_name = event.delta.tool_name_delta
                            yield {
                                "type": "tool_use_start",
                                "id": open_tool_call_id,
                                "name": open_tool_call_name or "",
                            }
                        args_delta = event.delta.args_delta
                        if args_delta:
                            args_str = (
                                args_delta
                                if isinstance(args_delta, str)
                                else json.dumps(args_delta, ensure_ascii=False)
                            )
                            yield {
                                "type": "tool_use_delta",
                                "id": delta_id,
                                "input_json": args_str,
                            }
                elif isinstance(event, FinalResultEvent):
                    # ``FinalResultEvent`` only fires for output-tool runs;
                    # the gateway uses ``output_mode='text'`` so this is a
                    # no-op. Keep the import to avoid a re-wire later.
                    pass

            try:
                final_response = response_stream.get()
            except Exception:  # pragma: no cover - provider partial failure
                final_response = None

            if open_tool_call_id is not None:
                yield {"type": "tool_use_stop", "id": open_tool_call_id}

            usage = response_stream.usage() if hasattr(response_stream, "usage") else None
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
            finish_reason = (
                response_stream.finish_reason if hasattr(response_stream, "finish_reason") else None
            )
            yield {
                "type": "stop",
                "stop_reason": _map_finish_reason(finish_reason, has_tool_use=has_tool_use),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
    except Exception as exc:  # pragma: no cover - provider partial failure
        log.warning("protocol_kernel stream failed: %s", exc)
        yield {"type": "error", "message": str(exc)}

    _ = final_response  # explicit hand-off so linters don't flag it
    return


__all__ = [
    "ProtocolKernelError",
    "run_kernel_one_shot",
    "run_kernel_stream",
]
