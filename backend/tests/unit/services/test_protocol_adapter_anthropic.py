"""Unit tests for the Anthropic Messages branch of ``protocol_adapter``.

Covers the four axes the M3.8 spec mandates:

* Plain text round-trip.
* Image (base64 + URL) input → ``image_data`` / ``image_url`` parts.
* Document input → ``file_data`` / ``file_url`` parts.
* ``tool_use`` ↔ ``tool_result`` content blocks → normalized parts.
* ``system`` as string vs as list of content blocks.
* Response shaper produces the expected Anthropic envelope.
"""

from __future__ import annotations

import json

import pytest

from app.services.protocol_adapter import (
    NormalizedMessageRequest,
    anthropic_messages_to_normalized,
    estimate_tokens_for_normalized,
    normalized_to_anthropic_response,
    stream_anthropic_messages,
)


# ─── Translation: request → normalized ──────────────────────
def test_text_only_request_round_trips() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": [{"type": "text", "text": "what now?"}]},
            ],
        }
    )
    assert norm.model == "claude-sonnet-4"
    assert norm.max_tokens == 1024
    assert len(norm.messages) == 3
    assert norm.messages[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }
    assert norm.messages[2]["content"][0]["text"] == "what now?"


def test_string_system_block_kept() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert norm.system == "You are a helpful assistant."


def test_list_system_block_concatenated() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "system": [
                {"type": "text", "text": "Persona"},
                {"type": "text", "text": "Constraints"},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert norm.system == "Persona\n\nConstraints"


def test_image_base64_block_translated() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgo=",
                            },
                        },
                        {"type": "text", "text": "what is this?"},
                    ],
                }
            ],
        }
    )
    parts = norm.messages[0]["content"]
    assert parts[0] == {
        "type": "image_data",
        "media_type": "image/png",
        "data": "iVBORw0KGgo=",
    }
    assert parts[1]["text"] == "what is this?"


def test_image_url_block_translated() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/cat.png",
                            },
                        }
                    ],
                }
            ],
        }
    )
    assert norm.messages[0]["content"][0] == {
        "type": "image_url",
        "url": "https://example.com/cat.png",
    }


def test_document_block_translated() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "title": "Q3 plan",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": "JVBERi0=",
                            },
                        }
                    ],
                }
            ],
        }
    )
    part = norm.messages[0]["content"][0]
    assert part["type"] == "file_data"
    assert part["media_type"] == "application/pdf"
    assert part["name"] == "Q3 plan"


def test_tool_use_round_trip_assistant_to_user() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "tools": [
                {
                    "name": "search",
                    "description": "Web search",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                }
            ],
            "messages": [
                {"role": "user", "content": "search foo"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "search",
                            "input": {"q": "foo"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "hits"}],
                        }
                    ],
                },
            ],
        }
    )
    tool_use = norm.messages[1]["content"][0]
    assert tool_use == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "search",
        "input": {"q": "foo"},
    }
    tool_result = norm.messages[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"
    assert tool_result["content"] == [{"type": "text", "text": "hits"}]
    assert norm.tools[0].name == "search"
    assert norm.tools[0].parameters_schema["properties"]["q"]["type"] == "string"


def test_tool_choice_variants_translated() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "any"},
        }
    )
    assert norm.tool_choice == "required"

    norm2 = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "tool", "name": "search"},
        }
    )
    assert norm2.tool_choice == {"type": "tool", "name": "search"}


def test_invalid_body_raises() -> None:
    with pytest.raises(ValueError):
        anthropic_messages_to_normalized({"model": "x"})  # no messages
    with pytest.raises(ValueError):
        anthropic_messages_to_normalized(
            {"messages": [{"role": "user", "content": "hi"}]}
        )  # no model
    with pytest.raises(ValueError):
        anthropic_messages_to_normalized([])  # not a dict


# ─── Translation: internal → Anthropic envelope ─────────────
def test_response_envelope_text_only() -> None:
    req = NormalizedMessageRequest(model="claude-sonnet-4", messages=[])
    out = normalized_to_anthropic_response(
        {
            "output_text": "hello",
            "tool_uses": [],
            "usage": {"input_tokens": 12, "output_tokens": 3},
            "stop_reason": "end_turn",
            "model": "claude-sonnet-4",
        },
        request=req,
    )
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["content"] == [{"type": "text", "text": "hello"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert out["id"].startswith("msg_")


def test_response_envelope_tool_use() -> None:
    req = NormalizedMessageRequest(model="claude-sonnet-4", messages=[])
    out = normalized_to_anthropic_response(
        {
            "output_text": "I'll search.",
            "tool_uses": [{"id": "toolu_42", "name": "search", "input": {"q": "foo"}}],
            "usage": {"input_tokens": 8, "output_tokens": 5},
            "stop_reason": "tool_use",
            "model": "claude-sonnet-4",
        },
        request=req,
    )
    blocks = out["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1] == {
        "type": "tool_use",
        "id": "toolu_42",
        "name": "search",
        "input": {"q": "foo"},
    }
    assert out["stop_reason"] == "tool_use"


# ─── Token estimator ────────────────────────────────────────
def test_token_estimator_counts_text_and_tools() -> None:
    norm = anthropic_messages_to_normalized(
        {
            "model": "claude-sonnet-4",
            "system": "x" * 40,
            "tools": [
                {
                    "name": "search",
                    "description": "web",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "messages": [{"role": "user", "content": "y" * 80}],
        }
    )
    est = estimate_tokens_for_normalized(norm)
    assert est >= 30  # 40/4 + 80/4 + tool blocks
    assert est < 200


# ─── SSE streaming encoder ──────────────────────────────────
async def _drain_sse(chunks_iter):
    out: list[bytes] = []
    async for chunk in chunks_iter:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_stream_text_only_emits_message_lifecycle() -> None:
    req = NormalizedMessageRequest(model="claude-sonnet-4", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "claude-sonnet-4"}
        yield {"type": "text_delta", "text": "Hel"}
        yield {"type": "text_delta", "text": "lo"}
        yield {
            "type": "stop",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }

    chunks = await _drain_sse(stream_anthropic_messages(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "event: message_start" in body
    assert "event: content_block_start" in body
    assert "event: content_block_delta" in body
    assert "event: content_block_stop" in body
    assert "event: message_delta" in body
    assert "event: message_stop" in body

    # message_delta carries usage + stop_reason
    delta_lines = [
        line for line in body.splitlines() if line.startswith("data:") and "message_delta" in line
    ]
    payload = json.loads(delta_lines[0][5:].strip())
    assert payload["delta"]["stop_reason"] == "end_turn"
    assert payload["usage"] == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.asyncio
async def test_stream_with_tool_use_emits_input_json_delta() -> None:
    req = NormalizedMessageRequest(model="claude-sonnet-4", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "claude-sonnet-4"}
        yield {"type": "tool_use_start", "id": "toolu_1", "name": "search"}
        yield {"type": "tool_use_delta", "id": "toolu_1", "input_json": '{"q":"x"}'}
        yield {"type": "tool_use_stop", "id": "toolu_1"}
        yield {
            "type": "stop",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }

    chunks = await _drain_sse(stream_anthropic_messages(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "input_json_delta" in body
    assert '"toolu_1"' in body
    assert "tool_use" in body


@pytest.mark.asyncio
async def test_stream_error_terminates_with_error_event() -> None:
    req = NormalizedMessageRequest(model="claude-sonnet-4", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "claude-sonnet-4"}
        yield {"type": "error", "message": "boom"}

    chunks = await _drain_sse(stream_anthropic_messages(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "event: error" in body
    assert "boom" in body
