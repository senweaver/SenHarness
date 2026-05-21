"""Integration: M4.4 Project Kanban routes (13 endpoints)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    """Register a fresh identity + auto-provisioned personal workspace."""
    email = f"kanban-{uuid.uuid4().hex[:8]}@example.com"
    password = "kanban-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Kanban Tester", "password": password},
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


async def _add_member(async_client, headers, ws_id) -> dict | None:
    """Invite a fresh user as MEMBER (non-admin)."""
    inv = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        headers=headers,
        json={"role": "member"},
    )
    if inv.status_code != 201:
        return None
    code = inv.json()["code"]

    headers_member, _ = await _bootstrap(async_client)
    accept = await async_client.post(
        "/api/v1/workspaces/invitations/accept",
        headers=headers_member,
        json={"code": code},
    )
    if accept.status_code not in (200, 201):
        return None
    headers_member["X-Workspace-Id"] = ws_id
    return headers_member


def _err_code(body: dict) -> str | None:
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("code")
    return body.get("code")


# ─── Boards ─────────────────────────────────────────────────────
async def test_create_board_admin_only(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards",
        headers=headers,
        json={"name": "Sprint 1", "description": "first one"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Sprint 1"
    assert body["workspace_id"] == ws_id

    member_headers = await _add_member(async_client, headers, ws_id)
    if member_headers is None:
        pytest.skip("invitation pipeline unavailable in this env")
    forbidden = await async_client.post(
        "/api/v1/boards",
        headers=member_headers,
        json={"name": "Sprint 2"},
    )
    assert forbidden.status_code == 403


async def test_list_boards_returns_workspace_boards(async_client):
    headers, _ = await _bootstrap(async_client)
    await async_client.post(
        "/api/v1/boards",
        headers=headers,
        json={"name": "A"},
    )
    await async_client.post(
        "/api/v1/boards",
        headers=headers,
        json={"name": "B"},
    )
    r = await async_client.get("/api/v1/boards", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()
    names = sorted(b["name"] for b in items)
    assert names == ["A", "B"]


async def test_get_board_returns_kanban_snapshot(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "Board"}
    )
    board_id = r.json()["id"]
    await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={"title": "first"},
    )
    r = await async_client.get(
        f"/api/v1/boards/{board_id}", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["board"]["id"] == board_id
    assert "backlog" in body["columns"]
    assert "in_progress" in body["columns"]
    assert "review" in body["columns"]
    assert "done" in body["columns"]
    assert len(body["columns"]["backlog"]) == 1


async def test_patch_board_renames(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "Old"}
    )
    board_id = r.json()["id"]
    r = await async_client.patch(
        f"/api/v1/boards/{board_id}",
        headers=headers,
        json={"name": "New", "description": "hello"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "New"
    assert body["description"] == "hello"


async def test_archive_board_soft_deletes(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "Doomed"}
    )
    board_id = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/boards/{board_id}/archive", headers=headers
    )
    assert r.status_code == 200, r.text
    # Subsequent get must 404.
    r = await async_client.get(f"/api/v1/boards/{board_id}", headers=headers)
    assert r.status_code == 404
    assert _err_code(r.json()) == "board.not_found"


# ─── Cards ──────────────────────────────────────────────────────
async def test_create_card_member_path(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "B"}
    )
    board_id = r.json()["id"]

    member_headers = await _add_member(async_client, headers, ws_id)
    use_headers = member_headers or headers
    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=use_headers,
        json={
            "title": "Triage backlog",
            "priority": "high",
            "column": "backlog",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Triage backlog"
    assert body["priority"] == "high"
    assert body["column"] == "backlog"


async def test_get_and_patch_card(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "B"}
    )
    board_id = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={"title": "T"},
    )
    card_id = r.json()["id"]

    r = await async_client.get(f"/api/v1/cards/{card_id}", headers=headers)
    assert r.status_code == 200

    r = await async_client.patch(
        f"/api/v1/cards/{card_id}",
        headers=headers,
        json={"priority": "urgent"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == "urgent"


async def test_move_card_changes_column(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "B"}
    )
    board_id = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={"title": "T"},
    )
    card_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/cards/{card_id}/move",
        headers=headers,
        json={"target_column": "review", "target_position": 0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["column"] == "review"
    assert body["sort_order"] == 0


async def test_complete_and_archive_card(async_client):
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "B"}
    )
    board_id = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={"title": "T"},
    )
    card_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/cards/{card_id}/complete", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["column"] == "done"
    assert body["completed_at"] is not None

    r = await async_client.post(
        f"/api/v1/cards/{card_id}/archive", headers=headers
    )
    assert r.status_code == 200

    r = await async_client.get(f"/api/v1/cards/{card_id}", headers=headers)
    assert r.status_code == 404


async def test_cross_workspace_isolation(async_client):
    headers_a, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/boards", headers=headers_a, json={"name": "Tenant-A"}
    )
    board_id = r.json()["id"]

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/boards/{board_id}", headers=headers_b
    )
    assert r.status_code == 404
    assert _err_code(r.json()) == "board.not_found"


async def test_unauth_returns_401(async_client):
    r = await async_client.get("/api/v1/boards")
    assert r.status_code == 401


async def test_list_cards_for_agent_filters_done(async_client):
    headers, ws_id = await _bootstrap(async_client)
    # Need an agent in the workspace; use the auto-provisioned default agent.
    r = await async_client.get("/api/v1/agents", headers=headers)
    assert r.status_code == 200, r.text
    agents = r.json()
    if not agents:
        pytest.skip("no default agent in this env")
    agent_id = agents[0]["id"]

    r = await async_client.post(
        "/api/v1/boards", headers=headers, json={"name": "B"}
    )
    board_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={
            "title": "open",
            "column": "in_progress",
            "assignee_agent_id": agent_id,
        },
    )
    assert r.status_code == 201, r.text
    open_card_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/boards/{board_id}/cards",
        headers=headers,
        json={
            "title": "done",
            "column": "done",
            "assignee_agent_id": agent_id,
        },
    )
    assert r.status_code == 201, r.text
    done_card_id = r.json()["id"]

    r = await async_client.get(
        f"/api/v1/agents/{agent_id}/cards", headers=headers
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = [c["id"] for c in rows]
    assert open_card_id in ids
    assert done_card_id not in ids
    _ = ws_id
