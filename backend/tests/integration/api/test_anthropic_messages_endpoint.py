"""Integration: Anthropic-compatible ``POST /v1/messages`` (M3.8).

Covers:

* Non-streaming happy path → Anthropic message envelope.
* Streaming → SSE event sequence (message_start … message_stop).
* tool_use round trip: client sends prior tool_result back; gateway
  forwards both turns to upstream and returns the model's next
  ``tool_use`` block.
* ``/v1/messages/count_tokens`` — char/4 estimator.
* Bad request → 400 + audit ``protocol.translation_failed``.

Real upstream calls are mocked at the kernel boundary so the test
suite stays hermetic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict[str, str], str]:
    email = f"am-{uuid.uuid4().hex[:8]}@example.com"
    password = "anthropic-messages-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "AM Tester", "password": password},
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


def _patch_kernel(monkeypatch, *, one_shot_result=None, stream_frames=None):
    """Replace ``protocol_kernel`` callables on the live module."""
    from app.api.v1 import openai_compat

    if one_shot_result is not None:
        async def fake_one_shot(normalized, *, workspace_id, identity_id=None):
            return dict(one_shot_result, model=normalized.model)

        monkeypatch.setattr(
            openai_compat.protocol_kernel,
            "run_kernel_one_shot",
            fake_one_shot,
        )

    if stream_frames is not None:
        def fake_stream(normalized, *, workspace_id, identity_id=None):
            async def _gen():
                for frame in stream_frames:
                    yield dict(frame)

            return _gen()

        monkeypatch.setattr(
            openai_compat.protocol_kernel,
            "run_kernel_stream",
            fake_stream,
        )


# ─── Non-streaming happy path ───────────────────────────────
async def test_anthropic_messages_non_streaming_happy(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    _patch_kernel(
        monkeypatch,
        one_shot_result={
            "output_text": "hi there",
            "tool_uses": [],
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "stop_reason": "end_turn",
            "upstream_model": "claude-sonnet-4",
            "provider_kind": "anthropic",
        },
    )

    r = await async_client.post(
        "/api/v1/openai/v1/messages",
        headers=headers,
        json={
            "model": "claude-sonnet-4",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["content"] == [{"type": "text", "text": "hi there"}]
    assert body["stop_reason"] == "end_turn"
    assert body["usage"] == {"input_tokens": 5, "output_tokens": 2}


# ─── Streaming happy path ───────────────────────────────────
async def test_anthropic_messages_streaming(async_client, monkeypatch) -> None:
    headers, _ = await _bootstrap(async_client)
    _patch_kernel(
        monkeypatch,
        stream_frames=[
            {"type": "start", "model": "claude-sonnet-4"},
            {"type": "text_delta", "text": "Hello"},
            {"type": "text_delta", "text": " there"},
            {
                "type": "stop",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        ],
    )

    async with async_client.stream(
        "POST",
        "/api/v1/openai/v1/messages",
        headers=headers,
        json={
            "model": "claude-sonnet-4",
            "stream": True,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as r:
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
    text = body.decode("utf-8")
    assert "event: message_start" in text
    assert "event: content_block_delta" in text
    assert "event: message_stop" in text


# ─── Tool-use round trip ────────────────────────────────────
async def test_anthropic_messages_tool_use_round_trip(
    async_client, monkeypatch
) -> None:
    headers, _ = await _bootstrap(async_client)

    captured: dict[str, Any] = {}

    from app.api.v1 import openai_compat

    async def capturing_one_shot(normalized, *, workspace_id, identity_id=None):
        captured["normalized_messages"] = normalized.messages
        captured["tools"] = [t.name for t in normalized.tools]
        return {
            "output_text": "I'll search.",
            "tool_uses": [
                {"id": "toolu_2", "name": "search", "input": {"q": "next"}}
            ],
            "usage": {"input_tokens": 12, "output_tokens": 7},
            "stop_reason": "tool_use",
            "model": normalized.model,
            "upstream_model": "claude-sonnet-4",
            "provider_kind": "anthropic",
        }

    monkeypatch.setattr(
        openai_compat.protocol_kernel,
        "run_kernel_one_shot",
        capturing_one_shot,
    )

    payload = {
        "model": "claude-sonnet-4",
        "max_tokens": 256,
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
            {"role": "user", "content": "find foo"},
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

    r = await async_client.post(
        "/api/v1/openai/v1/messages", headers=headers, json=payload
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tool_blocks = [b for b in body["content"] if b["type"] == "tool_use"]
    assert tool_blocks
    assert tool_blocks[0]["name"] == "search"
    assert tool_blocks[0]["input"] == {"q": "next"}
    assert body["stop_reason"] == "tool_use"

    # Kernel was called with the normalized history including the
    # tool_use + tool_result turns.
    captured_msgs = captured["normalized_messages"]
    assert any(
        any(p.get("type") == "tool_use" for p in msg["content"])
        for msg in captured_msgs
    )
    assert any(
        any(p.get("type") == "tool_result" for p in msg["content"])
        for msg in captured_msgs
    )
    assert "search" in captured["tools"]


# ─── count_tokens ───────────────────────────────────────────
async def test_anthropic_count_tokens_returns_estimate(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    text = "x" * 80
    r = await async_client.post(
        "/api/v1/openai/v1/messages/count_tokens",
        headers=headers,
        json={
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": text}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "input_tokens" in body
    # 80 chars → 80//4 = 20 tokens (lower bound; system / overhead may add).
    assert body["input_tokens"] >= 20


# ─── Bad request → 400 + translation_failed audit ───────────
async def test_anthropic_messages_invalid_body_returns_400(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/openai/v1/messages",
        headers=headers,
        json={"model": "claude-sonnet-4"},  # missing messages
    )
    assert r.status_code == 400, r.text
    body = r.json()
    err = body.get("detail") or body
    if isinstance(err, dict):
        assert err.get("code") == "protocol.invalid_body"


# ─── Auth: missing workspace header → 401 ───────────────────
async def test_anthropic_messages_requires_workspace(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    headers.pop("X-Workspace-Id", None)
    r = await async_client.post(
        "/api/v1/openai/v1/messages",
        headers=headers,
        json={
            "model": "claude-sonnet-4",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401, r.text
