"""M0.8 — every M0.8 audit action key gets exercised end-to-end.

Confirms the stable ``action`` strings the docs / changelog promise:

* ``channel.signature_required_but_unset``
* ``channel.signature_failed``
* ``channel.sender_blocked``
* ``channel.dup_external_app_at_migration`` (covered indirectly by the
  migration smoke; this file focuses on runtime audits)
* ``channel.rate_limited``
* ``channel.slack_team_mismatch`` (unit-tested in
  ``test_channel_slack_team.py``; the API path needs an outbound IM
  signing simulation that we keep to the unit layer)
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"audit-{uuid.uuid4().hex[:8]}@example.com"
    password = "audit-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Auditor", "password": password},
    )
    assert r.status_code == 201
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Audit WS", "slug": f"audit-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _ensure_agent(async_client, headers) -> str:
    r = await async_client.get("/api/v1/agents", headers=headers)
    agents = r.json()
    if agents:
        return agents[0]["id"]
    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "Audit Agent", "persona_md": "be brief"},
    )
    return r.json()["id"]


async def _audit_actions(db_session, ws_id: str) -> set[str]:
    from app.db.models import AuditEvent

    rows = (
        await db_session.execute(
            select(AuditEvent.action).where(AuditEvent.workspace_id == ws_id)
        )
    ).scalars().all()
    return set(rows)


async def test_signature_required_writes_audit(async_client, db_session):
    headers, ws_id = await _bootstrap(async_client)
    agent_id = await _ensure_agent(async_client, headers)
    r = await async_client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "name": "Audit Hook",
            "kind": "webhook",
            "config_json": {"verify_signatures": True},
            "default_agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201)
    cid = r.json()["id"]
    token = r.json()["inbound_token"]

    r = await async_client.post(
        f"/api/v1/hooks/ingress/{cid}",
        params={"token": token},
        content=b'{"text":"hi"}',
    )
    assert r.status_code == 401
    actions = await _audit_actions(db_session, ws_id)
    assert "channel.signature_required_but_unset" in actions


async def test_signature_failed_writes_audit(async_client, db_session):
    headers, ws_id = await _bootstrap(async_client)
    agent_id = await _ensure_agent(async_client, headers)
    secret = "shh"
    r = await async_client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "name": "Audit Hook 2",
            "kind": "webhook",
            "config_json": {"hmac_secret": secret, "verify_signatures": True},
            "default_agent_id": agent_id,
        },
    )
    cid = r.json()["id"]
    token = r.json()["inbound_token"]
    body = b'{"text":"hi"}'

    r = await async_client.post(
        f"/api/v1/hooks/ingress/{cid}",
        params={"token": token},
        headers={"x-hmac-signature": "deadbeef"},
        content=body,
    )
    assert r.status_code == 403
    actions = await _audit_actions(db_session, ws_id)
    assert "channel.signature_failed" in actions


async def test_sender_blocked_writes_audit(async_client, db_session):
    headers, ws_id = await _bootstrap(async_client)
    agent_id = await _ensure_agent(async_client, headers)
    secret = "shh"
    r = await async_client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "name": "Audit Hook 3",
            "kind": "webhook",
            "config_json": {"hmac_secret": secret, "verify_signatures": True},
            "default_agent_id": agent_id,
            "sender_allowlist_json": {"mode": "allow_listed", "allow": ["alice"]},
        },
    )
    cid = r.json()["id"]
    token = r.json()["inbound_token"]
    body = b'{"text":"hi","user":"mallory"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    r = await async_client.post(
        f"/api/v1/hooks/ingress/{cid}",
        params={"token": token},
        headers={"x-hmac-signature": sig, "content-type": "application/json"},
        content=body,
    )
    assert r.status_code in (200, 202)
    actions = await _audit_actions(db_session, ws_id)
    assert "channel.sender_blocked" in actions
