"""Integration: ``/insights`` is dispatched out-of-band from ``_handle_user_turn``.

Drives the WS turn handler directly with a fake WebSocket so we can
prove:

* the slash command never enters the agent loop (no kernel fan-out);
* the queue dispatcher fires with the expected ``days`` value;
* a confirmation system frame lands on the WS surface.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


class _FakeWebSocket:
    """Minimal WebSocket double for ``_handle_user_turn``.

    Captures every frame sent so the test can assert against the
    insights-queued confirmation. The ``close``/``accept`` pair are
    no-ops because ``_handle_user_turn`` doesn't drive the WS
    lifecycle directly.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        # Round-trip via JSON so the test catches non-serialisable
        # payloads (uuid, datetime, etc.) the same way the real loop
        # would.
        self.sent.append(json.loads(json.dumps(payload, default=str)))


async def _bootstrap_workspace(async_client) -> tuple[dict, str, str, str]:
    email = f"insights-ws-{uuid.uuid4().hex[:8]}@example.com"
    password = "insights-ws-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Insights WS", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Insights WS", "slug": f"iws-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    sid = r.json()["id"]
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    identity_id = str(decode_token(raw, expected_kind="access")["sub"])
    return headers, ws_id, sid, identity_id


async def test_insights_slash_command_bypasses_agent_loop(async_client):
    from app.api.v1.sessions import _handle_user_turn

    headers, ws_id, sid, identity_id = await _bootstrap_workspace(async_client)
    fake_ws = _FakeWebSocket()
    captured: dict = {}

    async def fake_queue(db, **kwargs):
        captured.update(kwargs)
        return {
            "queued": True,
            "days": int(kwargs.get("days") or 30),
            "expected_completion_seconds": 30,
            "job_id": "fake-job",
        }

    with patch(
        "app.services.cross_session_insights.queue_insights_generation",
        fake_queue,
    ):
        await _handle_user_turn(
            fake_ws,
            session_id=uuid.UUID(sid),
            workspace_id=uuid.UUID(ws_id),
            identity_id=uuid.UUID(identity_id),
            text="/insights",
        )

    # Queue dispatcher fired exactly once with the default day window
    # (the parser returned days=None; the service substitutes the
    # workspace default upstream of the mock).
    assert captured["workspace_id"] == uuid.UUID(ws_id)
    assert captured["identity_id"] == uuid.UUID(identity_id)
    assert captured["return_session_id"] == uuid.UUID(sid)
    assert captured["days"] is None

    # The WS surface received the system confirmation; no kernel
    # frames (delta / final / tool_call) were emitted because the
    # agent loop was bypassed.
    kinds = [
        (frame.get("type"), (frame.get("data") or {}).get("kind"))
        for frame in fake_ws.sent
    ]
    assert ("system", "insights_queued") in kinds
    assert all(t != "delta" for (t, _k) in kinds)
    assert all(t != "tool_call" for (t, _k) in kinds)
    assert all(t != "final" for (t, _k) in kinds)


async def test_insights_slash_command_with_days_flag(async_client):
    from app.api.v1.sessions import _handle_user_turn

    headers, ws_id, sid, identity_id = await _bootstrap_workspace(async_client)
    fake_ws = _FakeWebSocket()
    captured: dict = {}

    async def fake_queue(db, **kwargs):
        captured.update(kwargs)
        return {
            "queued": True,
            "days": int(kwargs.get("days") or 14),
            "expected_completion_seconds": 30,
            "job_id": None,
        }

    with patch(
        "app.services.cross_session_insights.queue_insights_generation",
        fake_queue,
    ):
        await _handle_user_turn(
            fake_ws,
            session_id=uuid.UUID(sid),
            workspace_id=uuid.UUID(ws_id),
            identity_id=uuid.UUID(identity_id),
            text="/insights --days 14",
        )

    assert captured["days"] == 14
    confirm = next(
        f for f in fake_ws.sent if f.get("type") == "system"
    )
    assert confirm["data"]["kind"] == "insights_queued"
    assert confirm["data"]["days"] == 14


async def test_insights_slash_command_breaker_open_emits_error_frame(
    async_client,
):
    from app.api.v1.sessions import _handle_user_turn
    from app.services.cross_session_insights import InsightsBreakerOpen

    headers, ws_id, sid, identity_id = await _bootstrap_workspace(async_client)
    fake_ws = _FakeWebSocket()

    async def fake_queue(db, **kwargs):
        _ = (db, kwargs)
        raise InsightsBreakerOpen("breaker open")

    with patch(
        "app.services.cross_session_insights.queue_insights_generation",
        fake_queue,
    ):
        await _handle_user_turn(
            fake_ws,
            session_id=uuid.UUID(sid),
            workspace_id=uuid.UUID(ws_id),
            identity_id=uuid.UUID(identity_id),
            text="/insights",
        )

    error_frames = [
        f for f in fake_ws.sent if f.get("type") == "error"
    ]
    assert len(error_frames) == 1
    assert error_frames[0]["data"]["code"] == "insights.breaker_open"
    assert error_frames[0]["data"]["retryable"] is True
