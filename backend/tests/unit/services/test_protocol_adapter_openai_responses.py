"""Unit tests for the OpenAI Responses branch of ``protocol_adapter``."""

from __future__ import annotations

import json

import pytest

from app.services.protocol_adapter import (
    NormalizedMessageRequest,
    normalized_to_openai_responses,
    openai_responses_to_normalized,
    stream_openai_responses,
)


# ─── Translation: request → normalized ──────────────────────
def test_string_input_becomes_user_message() -> None:
    norm = openai_responses_to_normalized({"model": "gpt-5", "input": "hello there"})
    assert norm.model == "gpt-5"
    assert norm.messages == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello there"}],
        }
    ]


def test_input_items_translate_text_image_file() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe these"},
                        {
                            "type": "input_image",
                            "image_url": "https://example.com/cat.png",
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,iVBORw0KGgo=",
                        },
                        {
                            "type": "input_file",
                            "filename": "report.pdf",
                            "file_data": "data:application/pdf;base64,JVBERi0=",
                        },
                    ],
                }
            ],
        }
    )
    parts = norm.messages[0]["content"]
    assert parts[0] == {"type": "text", "text": "describe these"}
    assert parts[1] == {"type": "image_url", "url": "https://example.com/cat.png"}
    assert parts[2] == {
        "type": "image_data",
        "media_type": "image/png",
        "data": "iVBORw0KGgo=",
    }
    assert parts[3]["type"] == "file_data"
    assert parts[3]["media_type"] == "application/pdf"
    assert parts[3]["name"] == "report.pdf"


def test_instructions_become_system() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "instructions": "Be concise.",
            "input": "hi",
        }
    )
    assert norm.system == "Be concise."


def test_developer_role_message_promoted_to_system() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": "Always answer in JSON.",
                },
                {"type": "message", "role": "user", "content": "go"},
            ],
        }
    )
    assert norm.system is not None
    assert "Always answer in JSON." in norm.system
    assert len(norm.messages) == 1
    assert norm.messages[0]["role"] == "user"


def test_function_tools_translated() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "input": "go",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Web",
                        "parameters": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                        },
                    },
                },
                {"type": "web_search_preview"},
            ],
            "tool_choice": "auto",
        }
    )
    assert norm.tools[0].name == "search"
    assert norm.tools[0].parameters_schema["properties"]["q"]["type"] == "string"
    assert norm.tools[1].name == "web_search_preview"
    assert norm.tool_choice == "auto"


def test_function_call_round_trip_preserves_call_id() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "input": [
                {"type": "message", "role": "user", "content": "search foo"},
                {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "name": "search",
                    "arguments": '{"q": "foo"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_abc",
                    "output": "hits",
                },
            ],
        }
    )
    # We expect 3 normalized messages: user, assistant tool_use, user tool_result
    assert len(norm.messages) == 3
    tool_use = norm.messages[1]["content"][0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["id"] == "call_abc"
    assert tool_use["input"] == {"q": "foo"}
    tool_result = norm.messages[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "call_abc"
    assert tool_result["content"] == [{"type": "text", "text": "hits"}]


def test_function_call_with_dict_arguments_kept_as_is() -> None:
    norm = openai_responses_to_normalized(
        {
            "model": "gpt-5",
            "input": [
                {"type": "message", "role": "user", "content": "go"},
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "search",
                    "arguments": {"q": "x"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "c1",
                    "output": "ok",
                },
            ],
        }
    )
    tool_use = norm.messages[1]["content"][0]
    assert tool_use["input"] == {"q": "x"}


def test_invalid_body_raises() -> None:
    with pytest.raises(ValueError):
        openai_responses_to_normalized({"model": "gpt-5", "input": []})
    with pytest.raises(ValueError):
        openai_responses_to_normalized({"input": "hi"})  # no model


# ─── Translation: internal → Responses envelope ─────────────
def test_response_envelope_text_only() -> None:
    req = NormalizedMessageRequest(model="gpt-5", messages=[])
    out = normalized_to_openai_responses(
        {
            "output_text": "answer",
            "tool_uses": [],
            "usage": {"input_tokens": 5, "output_tokens": 1},
            "stop_reason": "end_turn",
            "model": "gpt-5",
        },
        request=req,
    )
    assert out["object"] == "response"
    assert out["status"] == "completed"
    assert out["output_text"] == "answer"
    assert out["output"][0]["type"] == "message"
    assert out["output"][0]["content"][0]["type"] == "output_text"
    assert out["output"][0]["content"][0]["text"] == "answer"
    assert out["usage"]["total_tokens"] == 6


def test_response_envelope_with_function_call() -> None:
    req = NormalizedMessageRequest(model="gpt-5", messages=[])
    out = normalized_to_openai_responses(
        {
            "output_text": "I'll search.",
            "tool_uses": [{"id": "call_zzz", "name": "search", "input": {"q": "foo"}}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
            "stop_reason": "tool_use",
            "model": "gpt-5",
        },
        request=req,
    )
    items = out["output"]
    assert any(item["type"] == "message" for item in items)
    fc = next(item for item in items if item["type"] == "function_call")
    assert fc["call_id"] == "call_zzz"
    assert fc["name"] == "search"
    assert json.loads(fc["arguments"]) == {"q": "foo"}


# ─── SSE streaming encoder ──────────────────────────────────
async def _drain(chunks_iter):
    out: list[bytes] = []
    async for chunk in chunks_iter:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_stream_text_only_emits_response_event_sequence() -> None:
    req = NormalizedMessageRequest(model="gpt-5", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "gpt-5"}
        yield {"type": "text_delta", "text": "Ans"}
        yield {"type": "text_delta", "text": "wer"}
        yield {
            "type": "stop",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }

    chunks = await _drain(stream_openai_responses(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "event: response.created" in body
    assert "event: response.output_item.added" in body
    assert "event: response.output_text.delta" in body
    assert "event: response.output_text.done" in body
    assert "event: response.completed" in body
    completed_lines = [
        line
        for line in body.splitlines()
        if line.startswith("data:") and "response.completed" in line
    ]
    payload = json.loads(completed_lines[0][5:].strip())
    assert payload["response"]["status"] == "completed"
    assert payload["response"]["usage"]["total_tokens"] == 6


@pytest.mark.asyncio
async def test_stream_with_function_call_emits_arguments_delta() -> None:
    req = NormalizedMessageRequest(model="gpt-5", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "gpt-5"}
        yield {"type": "tool_use_start", "id": "call_a", "name": "search"}
        yield {"type": "tool_use_delta", "id": "call_a", "input_json": '{"q":"x"}'}
        yield {"type": "tool_use_stop", "id": "call_a"}
        yield {
            "type": "stop",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 4},
        }

    chunks = await _drain(stream_openai_responses(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "response.function_call_arguments.delta" in body
    assert "response.function_call_arguments.done" in body
    assert '"call_a"' in body


@pytest.mark.asyncio
async def test_stream_error_emits_response_failed() -> None:
    req = NormalizedMessageRequest(model="gpt-5", messages=[], stream=True)

    async def fake_kernel():
        yield {"type": "start", "model": "gpt-5"}
        yield {"type": "error", "message": "kaboom"}

    chunks = await _drain(stream_openai_responses(fake_kernel(), request=req))
    body = b"".join(chunks).decode("utf-8")
    assert "event: response.failed" in body
    assert "kaboom" in body
