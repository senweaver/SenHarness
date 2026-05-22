"""Protocol adapter: Anthropic Messages / OpenAI Responses ↔ internal format.

M3.8 — gateway translation layer that lets external agent frameworks
(Claude Code, Codex, etc.) talk to SenHarness as if it were Anthropic
or OpenAI directly. Three protocol entry points share one normalized
internal request shape; one kernel runs the upstream model in
**provider passthrough mode** (no SenHarness skill / memory / shields
injection — see :mod:`app.agents.kernels.protocol_kernel`); two
streaming encoders emit the protocol's native SSE event sequence.

The adapter is intentionally pure — translation only. All side
effects (auth, rate limit, audit, kernel run) live at the route
boundary in :mod:`app.api.v1.openai_compat`.

Tool-use translation matrix
---------------------------

============================================  ================================================================
Anthropic (request / response content block)  OpenAI Responses (input items / output items)
============================================  ================================================================
``{"type": "tool_use", "id", "name", "input"}``  ``{"type": "function_call", "call_id", "name", "arguments"}``
``{"type": "tool_result", "tool_use_id",``      ``{"type": "function_call_output", "call_id", "output"}``
``  "content"}``
``{"type": "text", "text"}``                     ``{"type": "input_text" | "output_text", "text"}``
``{"type": "image", "source": {...}}``           ``{"type": "input_image", "image_url" | "image_data"}``
``{"type": "document", "source": {...}}``        ``{"type": "input_file", "file_data" | "file_url"}``
============================================  ================================================================

Both shapes are normalized to the internal ``messages`` list, where
every entry is ``{"role", "content": [<part>], "tool_calls"?, ``
``"tool_call_id"?}`` and every part is one of ``text`` /
``image_data`` / ``image_url`` / ``file_data`` / ``file_url`` /
``tool_use`` / ``tool_result``. The kernel re-projects this onto
pydantic-ai's ``ModelMessage`` graph.

SSE event sequences
-------------------

Anthropic Messages stream::

    event: message_start
    data: {"type":"message_start","message":{...}}

    event: content_block_start    (one per text/tool_use block)
    data: {"type":"content_block_start","index":0,"content_block":{...}}

    event: content_block_delta    (zero or more per block)
    data: {"type":"content_block_delta","index":0,"delta":{...}}

    event: content_block_stop
    data: {"type":"content_block_stop","index":0}

    event: message_delta          (final usage + stop_reason)
    data: {"type":"message_delta","delta":{...},"usage":{...}}

    event: message_stop
    data: {"type":"message_stop"}

OpenAI Responses stream::

    event: response.created
    data: {...}

    event: response.output_item.added       (per item, e.g. message)
    data: {...}

    event: response.output_text.delta       (one per text token chunk)
    data: {"type":"response.output_text.delta","delta":"...",}

    event: response.output_text.done
    data: {...}

    event: response.output_item.done
    data: {...}

    event: response.completed
    data: {...,"response":{...,"usage":{...}}}
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# ─── Normalized internal shape ───────────────────────────────
@dataclass(slots=True)
class NormalizedTool:
    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedMessageRequest:
    """Single canonical request shape for both Anthropic and OpenAI gateways.

    ``messages`` parts are normalized dicts with stable ``type`` values:

    * ``{"type": "text", "text": "..."}``
    * ``{"type": "image_data", "media_type": "...", "data": "<b64>"}``
    * ``{"type": "image_url", "url": "..."}``
    * ``{"type": "file_data", "media_type": "...", "data": "<b64>",``
      ``"name"?: "..."}``
    * ``{"type": "file_url", "url": "...", "name"?: "..."}``
    * ``{"type": "tool_use", "id": "...", "name": "...",``
      ``"input": {...}}`` (assistant turn)
    * ``{"type": "tool_result", "tool_use_id": "...",``
      ``"content": [<text_part>...]}`` (user turn)

    The kernel reads every assistant message at the tail to decide
    whether the previous turn ended in tool calls; for protocol
    passthrough we do not execute those tool calls — we let the
    external framework carry that responsibility.
    """

    model: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    system: str | None = None
    tools: list[NormalizedTool] = field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    stop_sequences: list[str] = field(default_factory=list)


# ─── Translation: Anthropic Messages → Normalized ────────────
def _anthropic_part_to_normalized(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        if isinstance(part, str):
            return {"type": "text", "text": part}
        return None
    kind = part.get("type")
    if kind == "text":
        return {"type": "text", "text": str(part.get("text") or "")}
    if kind == "image":
        source = part.get("source") or {}
        if not isinstance(source, dict):
            return None
        if source.get("type") == "base64":
            return {
                "type": "image_data",
                "media_type": str(source.get("media_type") or "image/png"),
                "data": str(source.get("data") or ""),
            }
        if source.get("type") == "url":
            return {"type": "image_url", "url": str(source.get("url") or "")}
        return None
    if kind == "document":
        source = part.get("source") or {}
        if not isinstance(source, dict):
            return None
        name = part.get("title") or part.get("name")
        if source.get("type") == "base64":
            return {
                "type": "file_data",
                "media_type": str(source.get("media_type") or "application/pdf"),
                "data": str(source.get("data") or ""),
                "name": str(name) if name else None,
            }
        if source.get("type") == "url":
            return {
                "type": "file_url",
                "url": str(source.get("url") or ""),
                "name": str(name) if name else None,
            }
        if source.get("type") == "text":
            return {"type": "text", "text": str(source.get("data") or "")}
        return None
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": str(part.get("id") or ""),
            "name": str(part.get("name") or ""),
            "input": part.get("input") or {},
        }
    if kind == "tool_result":
        raw_content = part.get("content")
        result_parts: list[dict[str, Any]] = []
        if isinstance(raw_content, str):
            result_parts.append({"type": "text", "text": raw_content})
        elif isinstance(raw_content, list):
            for sub in raw_content:
                norm = _anthropic_part_to_normalized(sub)
                if norm is not None:
                    result_parts.append(norm)
        return {
            "type": "tool_result",
            "tool_use_id": str(part.get("tool_use_id") or ""),
            "is_error": bool(part.get("is_error")),
            "content": result_parts,
        }
    return None


def _coerce_anthropic_system(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, list):
        chunks: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                if entry:
                    chunks.append(entry)
            elif isinstance(entry, dict) and entry.get("type") == "text":
                text = entry.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
        joined = "\n\n".join(chunks)
        return joined or None
    return None


def _coerce_anthropic_tools(raw: Any) -> list[NormalizedTool]:
    if not isinstance(raw, list):
        return []
    out: list[NormalizedTool] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = entry.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        out.append(
            NormalizedTool(
                name=name,
                description=str(entry.get("description") or ""),
                parameters_schema=schema,
            )
        )
    return out


def _coerce_anthropic_tool_choice(raw: Any) -> str | dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        kind = raw.get("type")
        if kind == "auto":
            return "auto"
        if kind == "any":
            return "required"
        if kind == "tool":
            return {"type": "tool", "name": str(raw.get("name") or "")}
        if kind == "none":
            return "none"
    return None


def anthropic_messages_to_normalized(body: dict[str, Any]) -> NormalizedMessageRequest:
    """Translate an Anthropic ``POST /v1/messages`` body to the internal shape.

    Raises :class:`ValueError` when the body is unparseable; callers
    map that to a 400 response.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("messages must be a non-empty array")

    messages: list[dict[str, Any]] = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        if role not in ("user", "assistant"):
            continue
        content = raw.get("content")
        parts: list[dict[str, Any]] = []
        if isinstance(content, str):
            parts.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                norm = _anthropic_part_to_normalized(part)
                if norm is not None:
                    parts.append(norm)
        if not parts:
            continue
        messages.append({"role": role, "content": parts})

    if not messages:
        raise ValueError("messages contained no decodable content")

    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("model is required")

    raw_max = body.get("max_tokens")
    max_tokens = int(raw_max) if isinstance(raw_max, (int, float)) and raw_max > 0 else 4096

    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}

    return NormalizedMessageRequest(
        model=model,
        messages=messages,
        system=_coerce_anthropic_system(body.get("system")),
        tools=_coerce_anthropic_tools(body.get("tools")),
        tool_choice=_coerce_anthropic_tool_choice(body.get("tool_choice")),
        max_tokens=max_tokens,
        temperature=_coerce_optional_float(body.get("temperature")),
        top_p=_coerce_optional_float(body.get("top_p")),
        stream=bool(body.get("stream")),
        metadata=metadata or {},
        stop_sequences=_coerce_string_list(body.get("stop_sequences")),
    )


# ─── Translation: OpenAI Responses → Normalized ──────────────
def _openai_input_part_to_normalized(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        if isinstance(part, str):
            return {"type": "text", "text": part}
        return None
    kind = part.get("type")
    if kind in ("input_text", "output_text", "text"):
        return {"type": "text", "text": str(part.get("text") or "")}
    if kind == "input_image":
        url = part.get("image_url")
        if isinstance(url, str) and url.startswith("data:"):
            mime, _, data = _split_data_url(url)
            if data:
                return {"type": "image_data", "media_type": mime, "data": data}
        if isinstance(url, str) and url:
            return {"type": "image_url", "url": url}
        data = part.get("image_data")
        if isinstance(data, dict):
            return {
                "type": "image_data",
                "media_type": str(data.get("media_type") or "image/png"),
                "data": str(data.get("data") or ""),
            }
        return None
    if kind == "input_file":
        if isinstance(part.get("file_data"), str):
            data_url = part["file_data"]
            if data_url.startswith("data:"):
                mime, _, data = _split_data_url(data_url)
                if data:
                    return {
                        "type": "file_data",
                        "media_type": mime,
                        "data": data,
                        "name": str(part.get("filename") or "") or None,
                    }
            return {
                "type": "file_data",
                "media_type": str(part.get("media_type") or "application/octet-stream"),
                "data": str(part["file_data"]),
                "name": str(part.get("filename") or "") or None,
            }
        if isinstance(part.get("file_url"), str):
            return {
                "type": "file_url",
                "url": str(part["file_url"]),
                "name": str(part.get("filename") or "") or None,
            }
        return None
    return None


def _split_data_url(data_url: str) -> tuple[str, str, str]:
    """Return ``(mime, encoding, payload)`` from ``data:<mime>;base64,<payload>``."""
    if not data_url.startswith("data:"):
        return ("", "", "")
    head, _, payload = data_url.partition(",")
    head = head[5:]  # drop "data:"
    if ";" in head:
        mime, _, encoding = head.partition(";")
    else:
        mime, encoding = head, ""
    return (mime or "application/octet-stream", encoding, payload)


def _openai_message_item_to_normalized(item: dict[str, Any]) -> dict[str, Any] | None:
    role = item.get("role")
    if role not in ("user", "assistant", "system", "developer"):
        return None
    content = item.get("content")
    parts: list[dict[str, Any]] = []
    if isinstance(content, str):
        parts.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for sub in content:
            norm = _openai_input_part_to_normalized(sub)
            if norm is not None:
                parts.append(norm)
    if not parts:
        return None
    return {"role": role, "content": parts}


def openai_responses_to_normalized(body: dict[str, Any]) -> NormalizedMessageRequest:
    """Translate an OpenAI ``POST /v1/responses`` body to the internal shape."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    messages: list[dict[str, Any]] = []
    system_chunks: list[str] = []

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        system_chunks.append(instructions)

    raw_input = body.get("input")
    function_calls: list[dict[str, Any]] = []
    function_outputs: list[dict[str, Any]] = []

    if isinstance(raw_input, str) and raw_input:
        messages.append({"role": "user", "content": [{"type": "text", "text": raw_input}]})
    elif isinstance(raw_input, list):
        for entry in raw_input:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type == "message" or "role" in entry:
                norm = _openai_message_item_to_normalized(entry)
                if norm is None:
                    continue
                if norm["role"] in ("system", "developer"):
                    system_chunks.append(
                        "\n".join(
                            p.get("text", "") for p in norm["content"] if p.get("type") == "text"
                        )
                    )
                    continue
                messages.append({"role": norm["role"], "content": norm["content"]})
            elif entry_type == "function_call":
                function_calls.append(entry)
            elif entry_type == "function_call_output":
                function_outputs.append(entry)

    for call in function_calls:
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": str(call.get("call_id") or call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "input": _coerce_openai_arguments(call.get("arguments")),
                    }
                ],
            }
        )
    for out in function_outputs:
        output = out.get("output")
        if isinstance(output, str):
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": output}]
        elif isinstance(output, list):
            content_parts = []
            for sub in output:
                norm = _openai_input_part_to_normalized(sub)
                if norm is not None:
                    content_parts.append(norm)
        else:
            content_parts = [{"type": "text", "text": ""}]
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(out.get("call_id") or ""),
                        "is_error": False,
                        "content": content_parts,
                    }
                ],
            }
        )

    if not messages:
        raise ValueError("input must contain at least one message")

    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("model is required")

    return NormalizedMessageRequest(
        model=model,
        messages=messages,
        system="\n\n".join(s for s in system_chunks if s) or None,
        tools=_coerce_openai_tools(body.get("tools")),
        tool_choice=_coerce_openai_tool_choice(body.get("tool_choice")),
        max_tokens=int(body.get("max_output_tokens") or 4096),
        temperature=_coerce_optional_float(body.get("temperature")),
        top_p=_coerce_optional_float(body.get("top_p")),
        stream=bool(body.get("stream")),
        metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        stop_sequences=_coerce_string_list(body.get("stop")),
    )


def _coerce_openai_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {"_raw": raw}
        if isinstance(decoded, dict):
            return decoded
        return {"_raw": decoded}
    return {}


def _coerce_openai_tools(raw: Any) -> list[NormalizedTool]:
    if not isinstance(raw, list):
        return []
    out: list[NormalizedTool] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "function":
            spec = entry.get("function") if isinstance(entry.get("function"), dict) else entry
            name = spec.get("name") if isinstance(spec, dict) else None
            if not isinstance(name, str) or not name:
                continue
            description = str(spec.get("description") or "") if isinstance(spec, dict) else ""
            schema = spec.get("parameters") if isinstance(spec, dict) else None
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            out.append(NormalizedTool(name=name, description=description, parameters_schema=schema))
        elif kind in ("file_search", "web_search", "web_search_preview"):
            # Schema-only translation: pass through as a tool the
            # external framework can recognise. Real execution lives
            # outside M3.8 (unimplemented; documented).
            out.append(
                NormalizedTool(
                    name=str(kind),
                    description=f"Built-in {kind} (passthrough; not executed by SenHarness)",
                    parameters_schema={"type": "object", "properties": {}},
                )
            )
    return out


def _coerce_openai_tool_choice(raw: Any) -> str | dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        kind = raw.get("type")
        if kind in ("auto", "none", "required"):
            return kind
        if kind == "function":
            spec = raw.get("function") or {}
            name = spec.get("name") if isinstance(spec, dict) else raw.get("name")
            if isinstance(name, str) and name:
                return {"type": "tool", "name": name}
    return None


# ─── Helpers ────────────────────────────────────────────────
def _coerce_optional_float(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _coerce_string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, str) and s]
    return []


def estimate_tokens_char_div_4(text: str) -> int:
    """Char/4 token estimator — matches the budget heuristic used by
    M2.5.9 prompt sizing. Accurate enough for billing pre-flight; not
    a substitute for the upstream tokenizer.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_tokens_for_normalized(req: NormalizedMessageRequest) -> int:
    total = 0
    if req.system:
        total += estimate_tokens_char_div_4(req.system)
    for message in req.messages:
        for part in message.get("content") or ():
            if part.get("type") == "text":
                total += estimate_tokens_char_div_4(str(part.get("text") or ""))
            elif part.get("type") == "tool_use":
                total += estimate_tokens_char_div_4(
                    json.dumps(part.get("input") or {}, ensure_ascii=False)
                )
            elif part.get("type") == "tool_result":
                for sub in part.get("content") or ():
                    if sub.get("type") == "text":
                        total += estimate_tokens_char_div_4(str(sub.get("text") or ""))
            elif part.get("type") in ("image_data", "file_data"):
                # Vision / file payloads are billed per-pixel/per-page upstream;
                # the gateway uses a conservative flat estimate so the count
                # never *under*-reports.
                total += 256
            elif part.get("type") in ("image_url", "file_url"):
                total += 64
    for tool in req.tools:
        total += estimate_tokens_char_div_4(tool.description)
        total += estimate_tokens_char_div_4(json.dumps(tool.parameters_schema, ensure_ascii=False))
    return total


# ─── Translation: Internal → Anthropic Response ──────────────
def normalized_to_anthropic_response(
    internal: dict[str, Any],
    *,
    request: NormalizedMessageRequest,
) -> dict[str, Any]:
    """Build the Anthropic ``message`` JSON envelope from kernel output.

    ``internal`` is the dict returned by
    :func:`app.agents.kernels.protocol_kernel.run_kernel_one_shot`:

    .. code-block:: python

        {
            "output_text": str,
            "tool_uses": [{"id", "name", "input"}, ...],
            "usage": {"input_tokens": int, "output_tokens": int},
            "stop_reason": "end_turn" | "tool_use" | "max_tokens",
            "model": str,
        }
    """
    blocks: list[dict[str, Any]] = []
    text = str(internal.get("output_text") or "")
    if text:
        blocks.append({"type": "text", "text": text})
    for call in internal.get("tool_uses") or ():
        blocks.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or call.get("tool_call_id") or ""),
                "name": str(call.get("name") or ""),
                "input": call.get("input") or {},
            }
        )
    if not blocks:
        blocks.append({"type": "text", "text": ""})

    usage = internal.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": str(internal.get("model") or request.model),
        "content": blocks,
        "stop_reason": str(internal.get("stop_reason") or "end_turn"),
        "stop_sequence": internal.get("stop_sequence"),
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
    }


# ─── Translation: Internal → OpenAI Responses ────────────────
def normalized_to_openai_responses(
    internal: dict[str, Any],
    *,
    request: NormalizedMessageRequest,
) -> dict[str, Any]:
    """Build the OpenAI Responses JSON envelope from kernel output."""
    output_items: list[dict[str, Any]] = []
    text = str(internal.get("output_text") or "")
    msg_id = f"msg_{uuid.uuid4().hex}"
    if text:
        output_items.append(
            {
                "id": msg_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        )
    for call in internal.get("tool_uses") or ():
        output_items.append(
            {
                "id": f"fc_{uuid.uuid4().hex}",
                "type": "function_call",
                "status": "completed",
                "call_id": str(call.get("id") or call.get("tool_call_id") or ""),
                "name": str(call.get("name") or ""),
                "arguments": json.dumps(call.get("input") or {}, ensure_ascii=False),
            }
        )

    usage = internal.get("usage") or {}
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": str(internal.get("model") or request.model),
        "output": output_items,
        "output_text": text,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0),
        },
        "metadata": request.metadata,
    }


# ─── SSE encoders ───────────────────────────────────────────
def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


async def stream_anthropic_messages(
    internal_stream: AsyncIterator[dict[str, Any]],
    *,
    request: NormalizedMessageRequest,
) -> AsyncIterator[bytes]:
    """Encode the kernel's stream as Anthropic-style SSE.

    The kernel emits dict frames of the shape:

    * ``{"type": "start", "model": "..."}``
    * ``{"type": "text_delta", "text": "..."}``
    * ``{"type": "tool_use_start", "id", "name"}``
    * ``{"type": "tool_use_delta", "id", "input_json": "..."}``
    * ``{"type": "tool_use_stop", "id"}``
    * ``{"type": "stop", "stop_reason", "usage"}``
    * ``{"type": "error", "message"}``
    """
    msg_id = f"msg_{uuid.uuid4().hex}"
    started = False
    block_index = -1
    open_block_kind: str | None = None
    open_tool_id: str | None = None

    async def _close_open_block() -> bytes | None:
        nonlocal open_block_kind, open_tool_id
        if open_block_kind is None:
            return None
        out = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )
        open_block_kind = None
        open_tool_id = None
        return out

    try:
        async for frame in internal_stream:
            kind = frame.get("type")
            if kind == "start":
                started = True
                yield _sse_event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "model": str(frame.get("model") or request.model),
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )
            elif kind == "text_delta":
                if not started:
                    continue
                if open_block_kind != "text":
                    closed = await _close_open_block()
                    if closed:
                        yield closed
                    block_index += 1
                    open_block_kind = "text"
                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                yield _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "text_delta",
                            "text": str(frame.get("text") or ""),
                        },
                    },
                )
            elif kind == "tool_use_start":
                if not started:
                    continue
                closed = await _close_open_block()
                if closed:
                    yield closed
                block_index += 1
                open_block_kind = "tool_use"
                open_tool_id = str(frame.get("id") or "")
                yield _sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": open_tool_id,
                            "name": str(frame.get("name") or ""),
                            "input": {},
                        },
                    },
                )
            elif kind == "tool_use_delta":
                if not started or open_block_kind != "tool_use":
                    continue
                yield _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": str(frame.get("input_json") or ""),
                        },
                    },
                )
            elif kind == "tool_use_stop":
                if open_block_kind == "tool_use":
                    closed = await _close_open_block()
                    if closed:
                        yield closed
            elif kind == "stop":
                closed = await _close_open_block()
                if closed:
                    yield closed
                usage = frame.get("usage") or {}
                yield _sse_event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": str(frame.get("stop_reason") or "end_turn"),
                            "stop_sequence": frame.get("stop_sequence"),
                        },
                        "usage": {
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                        },
                    },
                )
                yield _sse_event("message_stop", {"type": "message_stop"})
            elif kind == "error":
                # Best-effort: close any open block and emit Anthropic
                # error envelope. Do not raise — the route handler has
                # already returned the StreamingResponse.
                closed = await _close_open_block()
                if closed:
                    yield closed
                yield _sse_event(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": "api_error",
                            "message": str(frame.get("message") or "stream_error"),
                        },
                    },
                )
                return
    finally:
        # Defensive: if the iterator was cancelled mid-stream we still
        # want a clean SSE termination so downstream proxies don't hang.
        if open_block_kind is not None:
            try:
                yield _sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": block_index},
                )
            except Exception:  # pragma: no cover
                pass


async def stream_openai_responses(
    internal_stream: AsyncIterator[dict[str, Any]],
    *,
    request: NormalizedMessageRequest,
) -> AsyncIterator[bytes]:
    """Encode the kernel's stream as OpenAI Responses SSE."""
    response_id = f"resp_{uuid.uuid4().hex}"
    msg_item_id = f"msg_{uuid.uuid4().hex}"
    output_index = -1
    open_kind: str | None = None
    open_tool_call_id: str | None = None
    function_item_id: str | None = None

    started = False
    output_text_buffer: list[str] = []

    def _response_envelope(*, status: str, completed: bool) -> dict[str, Any]:
        return {
            "id": response_id,
            "object": "response",
            "status": status,
            "model": request.model,
            "created_at": int(time.time()),
            "output": [],
            "metadata": request.metadata,
            "usage": (
                None
                if not completed
                else {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
            ),
        }

    try:
        async for frame in internal_stream:
            kind = frame.get("type")
            if kind == "start":
                started = True
                yield _sse_event(
                    "response.created",
                    {
                        "type": "response.created",
                        "response": _response_envelope(status="in_progress", completed=False),
                    },
                )
            elif kind == "text_delta":
                if not started:
                    continue
                if open_kind != "message":
                    if open_kind == "function_call":
                        yield _sse_event(
                            "response.function_call_arguments.done",
                            {
                                "type": "response.function_call_arguments.done",
                                "item_id": function_item_id,
                                "output_index": output_index,
                            },
                        )
                        yield _sse_event(
                            "response.output_item.done",
                            {
                                "type": "response.output_item.done",
                                "output_index": output_index,
                                "item": {
                                    "id": function_item_id,
                                    "type": "function_call",
                                    "status": "completed",
                                    "call_id": open_tool_call_id,
                                },
                            },
                        )
                    output_index += 1
                    open_kind = "message"
                    yield _sse_event(
                        "response.output_item.added",
                        {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {
                                "id": msg_item_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        },
                    )
                    yield _sse_event(
                        "response.content_part.added",
                        {
                            "type": "response.content_part.added",
                            "item_id": msg_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": "",
                                "annotations": [],
                            },
                        },
                    )
                text = str(frame.get("text") or "")
                output_text_buffer.append(text)
                yield _sse_event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "item_id": msg_item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": text,
                    },
                )
            elif kind == "tool_use_start":
                if open_kind == "message":
                    full = "".join(output_text_buffer)
                    yield _sse_event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "item_id": msg_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "text": full,
                        },
                    )
                    yield _sse_event(
                        "response.content_part.done",
                        {
                            "type": "response.content_part.done",
                            "item_id": msg_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": full,
                                "annotations": [],
                            },
                        },
                    )
                    yield _sse_event(
                        "response.output_item.done",
                        {
                            "type": "response.output_item.done",
                            "output_index": output_index,
                            "item": {
                                "id": msg_item_id,
                                "type": "message",
                                "status": "completed",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": full,
                                        "annotations": [],
                                    }
                                ],
                            },
                        },
                    )
                output_index += 1
                open_kind = "function_call"
                open_tool_call_id = str(frame.get("id") or "")
                function_item_id = f"fc_{uuid.uuid4().hex}"
                yield _sse_event(
                    "response.output_item.added",
                    {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": {
                            "id": function_item_id,
                            "type": "function_call",
                            "status": "in_progress",
                            "call_id": open_tool_call_id,
                            "name": str(frame.get("name") or ""),
                            "arguments": "",
                        },
                    },
                )
            elif kind == "tool_use_delta":
                if open_kind != "function_call":
                    continue
                yield _sse_event(
                    "response.function_call_arguments.delta",
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": function_item_id,
                        "output_index": output_index,
                        "delta": str(frame.get("input_json") or ""),
                    },
                )
            elif kind == "tool_use_stop":
                if open_kind == "function_call":
                    yield _sse_event(
                        "response.function_call_arguments.done",
                        {
                            "type": "response.function_call_arguments.done",
                            "item_id": function_item_id,
                            "output_index": output_index,
                        },
                    )
                    yield _sse_event(
                        "response.output_item.done",
                        {
                            "type": "response.output_item.done",
                            "output_index": output_index,
                            "item": {
                                "id": function_item_id,
                                "type": "function_call",
                                "status": "completed",
                                "call_id": open_tool_call_id,
                            },
                        },
                    )
                    open_kind = None
            elif kind == "stop":
                if open_kind == "message":
                    full = "".join(output_text_buffer)
                    yield _sse_event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "item_id": msg_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "text": full,
                        },
                    )
                    yield _sse_event(
                        "response.content_part.done",
                        {
                            "type": "response.content_part.done",
                            "item_id": msg_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": full,
                                "annotations": [],
                            },
                        },
                    )
                    yield _sse_event(
                        "response.output_item.done",
                        {
                            "type": "response.output_item.done",
                            "output_index": output_index,
                            "item": {
                                "id": msg_item_id,
                                "type": "message",
                                "status": "completed",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": full,
                                        "annotations": [],
                                    }
                                ],
                            },
                        },
                    )
                usage = frame.get("usage") or {}
                completed = _response_envelope(status="completed", completed=True)
                completed["usage"] = {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "total_tokens": int(usage.get("input_tokens") or 0)
                    + int(usage.get("output_tokens") or 0),
                }
                yield _sse_event(
                    "response.completed",
                    {"type": "response.completed", "response": completed},
                )
            elif kind == "error":
                yield _sse_event(
                    "response.failed",
                    {
                        "type": "response.failed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "failed",
                            "error": {
                                "code": "api_error",
                                "message": str(frame.get("message") or "stream_error"),
                            },
                            "model": request.model,
                        },
                    },
                )
                return
    finally:
        # Defensive flush analogous to the Anthropic encoder.
        pass


__all__ = [
    "NormalizedMessageRequest",
    "NormalizedTool",
    "anthropic_messages_to_normalized",
    "estimate_tokens_char_div_4",
    "estimate_tokens_for_normalized",
    "normalized_to_anthropic_response",
    "normalized_to_openai_responses",
    "openai_responses_to_normalized",
    "stream_anthropic_messages",
    "stream_openai_responses",
]
