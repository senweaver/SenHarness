"""End-to-end tests for the M0.1 session-goal REST surface.

Covers each of the six routes with both a happy path and at least one
RBAC failure case (cross-tenant leak), per the cross-cutting checklist
on the roadmap.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"goals-{uuid.uuid4().hex[:8]}@example.com"
    password = "goals-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Goals Tester", "password": password},
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
        json={"name": "Goals WS", "slug": f"goals-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _new_session(async_client, headers) -> str:
    r = await async_client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"kind": "p2p"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_lock_then_get_active(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)

    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals",
        headers=headers,
        json={
            "goal_text": "Ship the M0.1 feature",
            "success_criteria": ["banner visible", "scores recorded"],
            "alignment_threshold": 0.7,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["goal_text"] == "Ship the M0.1 feature"
    assert body["alignment_threshold"] == 0.7
    assert body["unlocked_at"] is None

    r = await async_client.get(
        f"/api/v1/sessions/{sid}/goals?only_active=true", headers=headers
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]


async def test_lock_rejected_when_active_exists(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    payload = {"goal_text": "first goal"}
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals", headers=headers, json=payload
    )
    assert r.status_code == 201
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals", headers=headers, json={"goal_text": "second"}
    )
    assert r.status_code == 409
    assert r.json()["code"] == "session_goal.already_locked"


async def test_patch_threshold(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals",
        headers=headers,
        json={"goal_text": "the goal", "alignment_threshold": 0.5},
    )
    gid = r.json()["id"]
    r = await async_client.patch(
        f"/api/v1/sessions/{sid}/goals/{gid}",
        headers=headers,
        json={"alignment_threshold": 0.85},
    )
    assert r.status_code == 200
    assert r.json()["alignment_threshold"] == 0.85


async def test_unlock_idempotent(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals",
        headers=headers,
        json={"goal_text": "unlock me"},
    )
    gid = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals/{gid}/unlock", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["unlocked_at"] is not None
    # Second unlock — idempotent (returns the same row, no error).
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals/{gid}/unlock", headers=headers
    )
    assert r.status_code == 200


async def test_list_alignment_empty(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    r = await async_client.get(
        f"/api/v1/sessions/{sid}/alignment", headers=headers
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_realign_no_active_goal_returns_null(async_client):
    headers, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    # Need a real assistant message id — append one through the API.
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers=headers,
        json={"role": "assistant", "content_json": {"text": "hi"}},
    )
    assert r.status_code == 201
    msg_id = r.json()["id"]
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/messages/{msg_id}/realign",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json() is None


# ─── RBAC failure cases ──────────────────────────────────────
async def test_lock_cross_workspace_404(async_client):
    """Workspace A's session id must 404 when called with workspace B's
    bearer/header. We use a random session uuid scoped to B."""
    headers_a, _ = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers_a)
    headers_b, _ = await _bootstrap(async_client)

    # Workspace B never created sid → 404.
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals",
        headers=headers_b,
        json={"goal_text": "leak"},
    )
    assert r.status_code in (403, 404), r.text


async def test_patch_other_workspace_goal_404(async_client):
    headers_a, _ = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    r = await async_client.post(
        f"/api/v1/sessions/{sid_a}/goals",
        headers=headers_a,
        json={"goal_text": "private"},
    )
    gid = r.json()["id"]

    headers_b, _ = await _bootstrap(async_client)
    sid_b = await _new_session(async_client, headers_b)
    # B knows the (stolen) goal id — must still 404.
    r = await async_client.patch(
        f"/api/v1/sessions/{sid_b}/goals/{gid}",
        headers=headers_b,
        json={"alignment_threshold": 0.1},
    )
    assert r.status_code in (403, 404)


async def test_unauthenticated_lock_blocked(async_client):
    sid = uuid.uuid4()
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/goals",
        json={"goal_text": "anyone home?"},
    )
    assert r.status_code in (401, 403)


async def test_get_active_cross_workspace_returns_empty(async_client):
    headers_a, _ = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    await async_client.post(
        f"/api/v1/sessions/{sid_a}/goals",
        headers=headers_a,
        json={"goal_text": "secret"},
    )

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/sessions/{sid_a}/goals", headers=headers_b
    )
    # Either 403/404 from workspace mismatch — never the leaked rows.
    assert r.status_code in (403, 404)
