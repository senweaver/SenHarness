"""M0.8 — same external bot in two channels returns 409.

Creating a discord channel with bot_token X, then a second channel
with the same bot_token, must fail with the typed
``channel.external_app_already_bound`` code so the frontend can
render the explicit copy.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> dict:
    email = f"chdup-{uuid.uuid4().hex[:8]}@example.com"
    password = "chdup-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Dup Bot", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Dup WS", "slug": f"chdup-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201)
    headers["X-Workspace-Id"] = r.json()["id"]
    return headers


async def _ensure_agent(async_client, headers) -> str:
    r = await async_client.get("/api/v1/agents", headers=headers)
    agents = r.json()
    if agents:
        return agents[0]["id"]
    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "Dup Agent", "persona_md": "be brief"},
    )
    return r.json()["id"]


async def test_duplicate_discord_bot_returns_typed_409(async_client):
    headers = await _bootstrap(async_client)
    agent_id = await _ensure_agent(async_client, headers)

    bot_token = f"discord-bot-{uuid.uuid4().hex}"
    public_key = "00" * 32
    body = {
        "name": "Discord A",
        "kind": "discord",
        "config_json": {
            "bot_token": bot_token,
            "public_key": public_key,
        },
        "default_agent_id": agent_id,
    }
    r1 = await async_client.post("/api/v1/channels", headers=headers, json=body)
    assert r1.status_code in (200, 201), r1.text

    body["name"] = "Discord B"
    r2 = await async_client.post("/api/v1/channels", headers=headers, json=body)
    assert r2.status_code == 409, r2.text
    detail = r2.json().get("detail")
    assert isinstance(detail, str)
    assert "external_app_already_bound" in detail
