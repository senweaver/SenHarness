"""End-to-end tests for the M1.10 skill diff REST surface.

Covers:
* ``POST /api/v1/skills/diff`` happy + RBAC failure (no workspace) +
  oversize input (422) + truncation flag in response.
* ``GET .../versions/{a}/diff/{b}`` returns 501 with the stable code
  ``skill.versions_not_implemented`` until M1.2 lands.
* Rate limit bucket (``skill_diff_compute``) trips at 30/60s.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"diff-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-diff-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Diff Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Diff WS", "slug": f"diff-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def test_compute_diff_happy_path(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/skills/diff",
        headers=headers,
        json={"old_content": "alpha\nbeta\n", "new_content": "alpha\nBETA\n"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is False
    assert body["files_changed"] == ["SKILL.md"]
    assert body["stats"]["added_lines"] == 1
    assert body["stats"]["removed_lines"] == 1
    assert "+BETA" in body["diff"]
    assert "-beta" in body["diff"]


async def test_compute_diff_identical_inputs_returns_empty(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/skills/diff",
        headers=headers,
        json={"old_content": "same\n", "new_content": "same\n"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["diff"] == ""
    assert body["stats"] == {"added_lines": 0, "removed_lines": 0, "hunks": 0}


async def test_compute_diff_no_workspace_header_rejected(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    bare = {k: v for k, v in headers.items() if k != "X-Workspace-Id"}
    r = await async_client.post(
        "/api/v1/skills/diff",
        headers=bare,
        json={"old_content": "a", "new_content": "b"},
    )
    assert r.status_code == 401
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "auth.no_active_workspace"


async def test_compute_diff_unauthenticated_blocked(async_client) -> None:
    r = await async_client.post(
        "/api/v1/skills/diff",
        json={"old_content": "a", "new_content": "b"},
    )
    assert r.status_code in (401, 403)


async def test_compute_diff_oversize_payload_rejected(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    big = "x" * (200_000 + 1)
    r = await async_client.post(
        "/api/v1/skills/diff",
        headers=headers,
        json={"old_content": big, "new_content": "x"},
    )
    assert r.status_code == 422


async def test_compute_diff_at_upper_size_bound_ok_and_truncated(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    old = "\n".join(f"line-{i}" for i in range(20_000))
    new = "\n".join(f"line-{i}-edited" for i in range(20_000))
    r = await async_client.post(
        "/api/v1/skills/diff",
        headers=headers,
        json={"old_content": old, "new_content": new},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True
    assert "diff truncated" in body["diff"]


async def test_versions_diff_unknown_pack_returns_404(async_client) -> None:
    """M1.2 swapped the 501 stub for a real lookup. A non-existent
    pack now resolves to 404 (``skill_pack.not_found``); the success
    case lives in ``test_skill_diff_versions_unblocked.py``.
    """
    headers, _ = await _bootstrap(async_client)
    pack_id = uuid.uuid4()
    r = await async_client.get(
        f"/api/v1/skills/packs/{pack_id}/versions/v1/diff/v2", headers=headers
    )
    assert r.status_code == 404, r.text
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "skill_pack.not_found"


async def test_versions_diff_unauth_blocked(async_client) -> None:
    pack_id = uuid.uuid4()
    r = await async_client.get(f"/api/v1/skills/packs/{pack_id}/versions/v1/diff/v2")
    assert r.status_code in (401, 403)


async def test_compute_diff_rate_limit_trips_429(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    last = None
    for _ in range(35):
        r = await async_client.post(
            "/api/v1/skills/diff",
            headers=headers,
            json={"old_content": "a\n", "new_content": "b\n"},
        )
        last = r.status_code
        if last == 429:
            break
    assert last == 429
