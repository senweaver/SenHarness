"""Integration: OpenAI-compatible ``POST /v1/responses`` (M3.8).

Mirrors :mod:`test_anthropic_messages_endpoint` for the OpenAI side:

* Non-streaming happy path → Responses envelope.
* Streaming → SSE event sequence (response.created … response.completed).
* function_call round trip (call_id ↔ tool_use_id).
* Vision (input_image data URL) reaches the kernel as image_data.
* Auth + 401.

The kernel is mocked so no real LLM key is required.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict[str, str], str]:
    email = f"or-{uuid.uuid4().hex[:8]}@example.com"
    password = "openai-responses-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "OR Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = r.json()["access_token"]
    workspace = body.get("workspace") or {}
    ws_id = workspace.get("id")
    headers = {"Authorization": f"Bearer {token}"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


def _patch_one_shot(monkeypatch, result):
    from app.api.v1 import openai_compat

    async def fake(normalized, *, workspace_id, identity_id=None):
        return dict(result, model=normalized.model)

    monkeypatch.setattr(openai_compat.protocol_kernel, "run_kernel_one_shot", fake)


def _patch_stream(monkeypatch, frames):
    from app.api.v1 import openai_compat

    def fake(normalized, *, workspace_id, identity_id=None):
        async def _gen():
            for frame in frames:
                yield dict(frame)

        return _gen()

    monkeypatch.setattr(openai_compat.protocol_kernel, "run_kernel_stream", fake)


# ─── Non-streaming happy path ───────────────────────────────
async def test_responses_non_streaming_happy(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    _patch_one_shot(
        monkeypatch,
        {
            "output_text": "answer",
            "tool_uses": [],
            "usage": {"input_tokens": 4, "output_tokens": 1},
            "stop_reason": "end_turn",
            "upstream_model": "gpt-5",
            "provider_kind": "openai",
        },
    )

    r = await async_client.post(
        "/api/v1/openai/v1/responses",
        headers=headers,
        json={"model": "gpt-5", "input": "hi"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output_text"] == "answer"
    assert body["usage"]["total_tokens"] == 5


# ─── Streaming happy path ───────────────────────────────────
async def test_responses_streaming(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    _patch_stream(
        monkeypatch,
        frames=[
            {"type": "start", "model": "gpt-5"},
            {"type": "text_delta", "text": "Ans"},
            {"type": "text_delta", "text": "wer"},
            {
                "type": "stop",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        ],
    )

    async with async_client.stream(
        "POST",
        "/api/v1/openai/v1/responses",
        headers=headers,
        json={"model": "gpt-5", "input": "hi", "stream": True},
    ) as r:
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert "event: response.created" in text
    assert "event: response.output_text.delta" in text
    assert "event: response.completed" in text


# ─── Function-call round trip ───────────────────────────────
async def test_responses_function_call_round_trip(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    captured: dict[str, Any] = {}

    from app.api.v1 import openai_compat

    async def fake(normalized, *, workspace_id, identity_id=None):
        captured["messages"] = normalized.messages
        return {
            "output_text": "",
            "tool_uses": [{"id": "call_y", "name": "search", "input": {"q": "next"}}],
            "usage": {"input_tokens": 6, "output_tokens": 4},
            "stop_reason": "tool_use",
            "model": normalized.model,
            "upstream_model": "gpt-5",
            "provider_kind": "openai",
        }

    monkeypatch.setattr(openai_compat.protocol_kernel, "run_kernel_one_shot", fake)

    r = await async_client.post(
        "/api/v1/openai/v1/responses",
        headers=headers,
        json={
            "model": "gpt-5",
            "input": [
                {"type": "message", "role": "user", "content": "find x"},
                {
                    "type": "function_call",
                    "call_id": "call_x",
                    "name": "search",
                    "arguments": '{"q": "x"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_x",
                    "output": "hits",
                },
            ],
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
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    fc_items = [item for item in body["output"] if item["type"] == "function_call"]
    assert fc_items
    assert fc_items[0]["call_id"] == "call_y"
    assert fc_items[0]["name"] == "search"

    # Kernel saw the prior function_call + function_call_output
    msgs = captured["messages"]
    assert any(any(p.get("type") == "tool_use" for p in m["content"]) for m in msgs)
    assert any(any(p.get("type") == "tool_result" for p in m["content"]) for m in msgs)


# ─── Vision (input_image data URL) ──────────────────────────
async def test_responses_vision_input_image_data_url(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    captured: dict[str, Any] = {}

    from app.api.v1 import openai_compat

    async def fake(normalized, *, workspace_id, identity_id=None):
        captured["messages"] = normalized.messages
        return {
            "output_text": "ok",
            "tool_uses": [],
            "usage": {"input_tokens": 8, "output_tokens": 1},
            "stop_reason": "end_turn",
            "model": normalized.model,
            "upstream_model": "gpt-5",
            "provider_kind": "openai",
        }

    monkeypatch.setattr(openai_compat.protocol_kernel, "run_kernel_one_shot", fake)

    r = await async_client.post(
        "/api/v1/openai/v1/responses",
        headers=headers,
        json={
            "model": "gpt-5",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe"},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,iVBORw0KGgo=",
                        },
                    ],
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    msg = captured["messages"][0]
    parts = msg["content"]
    assert any(p["type"] == "image_data" for p in parts)


# ─── Rate limit ─────────────────────────────────────────────
async def test_responses_rate_limit_eventually_429(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    _patch_one_shot(
        monkeypatch,
        {
            "output_text": "ok",
            "tool_uses": [],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "stop_reason": "end_turn",
            "model": "gpt-5",
            "upstream_model": "gpt-5",
            "provider_kind": "openai",
        },
    )

    saw_429 = False
    # The default budget is 60/60s; a tight burst above 60 hits the limit.
    for _ in range(80):
        r = await async_client.post(
            "/api/v1/openai/v1/responses",
            headers=headers,
            json={"model": "gpt-5", "input": "hi"},
        )
        if r.status_code == 429:
            saw_429 = True
            break
        assert r.status_code == 200, r.text
    assert saw_429, "expected the bucket to fire eventually"


# ─── Auth ───────────────────────────────────────────────────
async def test_responses_requires_workspace(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    headers.pop("X-Workspace-Id", None)
    r = await async_client.post(
        "/api/v1/openai/v1/responses",
        headers=headers,
        json={"model": "gpt-5", "input": "hi"},
    )
    assert r.status_code == 401, r.text
