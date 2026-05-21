"""M0.8 — channel ingress per-sender + per-channel rate limit + audit."""

from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"chsec-{uuid.uuid4().hex[:8]}@example.com"
    password = "chsec-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Channel Sec", "password": password},
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
        json={"name": "ChSec WS", "slug": f"chsec-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _create_webhook_channel(async_client, headers) -> tuple[str, str, str]:
    r = await async_client.get("/api/v1/agents", headers=headers)
    assert r.status_code == 200
    agents = r.json()
    if not agents:
        r = await async_client.post(
            "/api/v1/agents",
            headers=headers,
            json={"name": "Hook Agent", "persona_md": "be brief"},
        )
        assert r.status_code in (200, 201)
        agents = [r.json()]
    agent_id = agents[0]["id"]

    secret = "shh-secret"
    r = await async_client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "name": "Webhook RL",
            "kind": "webhook",
            "config_json": {"hmac_secret": secret, "verify_signatures": True},
            "default_agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    channel = r.json()
    return channel["id"], channel["inbound_token"], secret


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_signature_required_returns_401(async_client):
    headers, _ = await _bootstrap(async_client)
    cid, token, _ = await _create_webhook_channel(async_client, headers)

    body = b'{"text":"hi","user":"alice"}'
    r = await async_client.post(
        f"/api/v1/hooks/ingress/{cid}",
        params={"token": token},
        content=body,
    )
    assert r.status_code == 401
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "webhook.hmac_secret_unset"


async def test_per_sender_rate_limit_after_20(async_client):
    headers, _ = await _bootstrap(async_client)
    cid, token, secret = await _create_webhook_channel(async_client, headers)

    body = b'{"text":"hi","user":"alice"}'
    sig = _sign(secret, body)
    last_status = None
    for _ in range(21):
        r = await async_client.post(
            f"/api/v1/hooks/ingress/{cid}",
            params={"token": token},
            headers={"x-hmac-signature": sig, "content-type": "application/json"},
            content=body,
        )
        last_status = r.status_code
    assert last_status == 429


async def test_other_sender_not_blocked(async_client):
    headers, _ = await _bootstrap(async_client)
    cid, token, secret = await _create_webhook_channel(async_client, headers)

    alice = b'{"text":"hi","user":"alice"}'
    bob = b'{"text":"hi","user":"bob"}'
    sig_a = _sign(secret, alice)
    sig_b = _sign(secret, bob)

    for _ in range(20):
        await async_client.post(
            f"/api/v1/hooks/ingress/{cid}",
            params={"token": token},
            headers={"x-hmac-signature": sig_a, "content-type": "application/json"},
            content=alice,
        )
    r = await async_client.post(
        f"/api/v1/hooks/ingress/{cid}",
        params={"token": token},
        headers={"x-hmac-signature": sig_b, "content-type": "application/json"},
        content=bob,
    )
    assert r.status_code != 429
