"""Workspace + agent CRUD smoke — the single-company setup path.

Covers the 10-minute-quickstart flow end-to-end: identity → workspace
→ agent. If this breaks, a new company can't onboard, so any PR that
trips this test shouldn't merge.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap_admin(async_client) -> dict:
    """Register and log in a fresh identity; return auth headers."""
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    password = "admin-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Admin", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_workspace_create_lists_and_switches(async_client):
    headers = await _bootstrap_admin(async_client)

    slug = f"acme-{uuid.uuid4().hex[:6]}"
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme Corp", "slug": slug, "description": "test corp"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["slug"] == slug
    # V1 week 2 — workspace_type defaults to 'company'.
    assert body.get("workspace_type", "company") == "company"

    r = await async_client.get("/api/v1/workspaces", headers=headers)
    assert r.status_code == 200
    items = r.json()
    assert any(w["slug"] == slug for w in items)


async def test_agent_create_requires_workspace_header(async_client):
    """Creating an agent outside a workspace must fail cleanly."""
    headers = await _bootstrap_admin(async_client)
    # Don't include X-Workspace-Id → expect 401-style 'no_active_workspace'
    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "Helper"},
    )
    assert r.status_code in (401, 403)


async def test_agent_backend_kind_accepts_future_adapter(async_client):
    """V1 week 2 loosened ``backend_kind`` from a closed enum to any
    snake_case string — a community-shipped adapter like ``crewai``
    shouldn't be rejected at the schema layer. The registry handles
    the 'unknown adapter' case at invocation time with a descriptive
    error event.
    """
    headers = await _bootstrap_admin(async_client)
    slug = f"beta-{uuid.uuid4().hex[:6]}"
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Beta", "slug": slug},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]

    headers["X-Workspace-Id"] = ws_id
    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "Future Agent",
            "backend_kind": "hypothetical_future_runtime",
        },
    )
    # 201 created — schema accepts the unknown kind; the /agents/runtimes
    # check happens at run-time, not CRUD-time.
    assert r.status_code in (200, 201), r.text
    assert r.json()["backend_kind"] == "hypothetical_future_runtime"
