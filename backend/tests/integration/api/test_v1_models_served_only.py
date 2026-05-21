"""Integration: ``GET /v1/models`` lists only served names (M2.5.7).

The OpenAI-compatible models endpoint must enumerate the workspace's
served names — not the upstream provider model ids. Swapping a
provider behind an alias must not flap the listing.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"sm-{uuid.uuid4().hex[:8]}@example.com"
    password = "served-models-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Served Models Tester", "password": password},
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


# ─── /v1/models ──────────────────────────────────────────────


async def test_v1_models_empty_workspace(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.get(
        "/api/v1/openai/v1/models", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert body["data"] == []


async def test_v1_models_lists_alias_keys(async_client):
    headers, ws_id = await _bootstrap(async_client)
    for served, upstream in (
        ("ws-fast", "deepseek:deepseek-chat"),
        ("ws-thinking", "openai:gpt-5"),
    ):
        r = await async_client.put(
            f"/api/v1/workspaces/{ws_id}/settings/served-aliases/{served}",
            headers=headers,
            json={"upstream": upstream},
        )
        assert r.status_code == 200, r.text

    r = await async_client.get("/api/v1/openai/v1/models", headers=headers)
    assert r.status_code == 200, r.text
    ids = sorted(item["id"] for item in r.json()["data"])
    assert ids == ["ws-fast", "ws-thinking"]
    # Every entry advertises the model object shape.
    for item in r.json()["data"]:
        assert item["object"] == "model"
        assert item["owned_by"] == "senharness"


async def test_v1_models_lists_agent_served_names(async_client):
    headers, ws_id = await _bootstrap(async_client)

    # Create an agent + flip its served_model_name via direct DB write
    # (no admin endpoint exists yet for this column — the whole point
    # of M2.5.7 is to keep agent CRUD untouched and let services pick
    # served vs upstream).
    factory = get_session_factory()
    async with factory() as db:
        from app.services import agent as agent_svc

        a = await agent_svc.create_agent(
            db,
            workspace_id=uuid.UUID(ws_id),
            created_by=None,
            name="branded",
            description="x",
            persona_md="x",
        )
        a.served_model_name = "ws-branded"
        await db.commit()

    r = await async_client.get("/api/v1/openai/v1/models", headers=headers)
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["data"]]
    assert "ws-branded" in ids


async def test_v1_models_dedupes_overlap(async_client):
    """Agent served name + same alias key → single entry."""
    headers, ws_id = await _bootstrap(async_client)

    factory = get_session_factory()
    async with factory() as db:
        from app.services import agent as agent_svc

        a = await agent_svc.create_agent(
            db,
            workspace_id=uuid.UUID(ws_id),
            created_by=None,
            name="dual",
            description="x",
            persona_md="x",
        )
        a.served_model_name = "ws-fast"
        await db.commit()

    r = await async_client.put(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases/ws-fast",
        headers=headers,
        json={"upstream": "deepseek:deepseek-chat"},
    )
    assert r.status_code == 200, r.text

    r = await async_client.get("/api/v1/openai/v1/models", headers=headers)
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()["data"]]
    assert ids.count("ws-fast") == 1


async def test_v1_models_requires_workspace_header(async_client):
    headers, _ = await _bootstrap(async_client)
    headers.pop("X-Workspace-Id", None)
    r = await async_client.get("/api/v1/openai/v1/models", headers=headers)
    # No active workspace → ``auth.no_active_workspace`` (401) — exact
    # status comes from :func:`_require_workspace` in openai_compat.
    assert r.status_code == 401, r.text


# ─── alias map admin REST ───────────────────────────────────


async def test_alias_upsert_and_delete_round_trip(async_client):
    headers, ws_id = await _bootstrap(async_client)

    r = await async_client.put(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases/ws-fast",
        headers=headers,
        json={"upstream": "deepseek:deepseek-chat"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "served_name": "ws-fast",
        "upstream": "deepseek:deepseek-chat",
    }

    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases", headers=headers
    )
    assert r.status_code == 200, r.text
    aliases = r.json()["aliases"]
    assert {"served_name": "ws-fast", "upstream": "deepseek:deepseek-chat"} in aliases

    r = await async_client.delete(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases/ws-fast",
        headers=headers,
    )
    assert r.status_code == 204, r.text

    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["aliases"] == []


async def test_alias_upsert_validates_format(async_client):
    headers, ws_id = await _bootstrap(async_client)
    # Spaces in the served name are rejected at the schema layer.
    r = await async_client.put(
        f"/api/v1/workspaces/{ws_id}/settings/served-aliases/ws fast",
        headers=headers,
        json={"upstream": "deepseek:deepseek-chat"},
    )
    assert r.status_code == 422, r.text
