"""Integration: M1.10 ``GET /skills/packs/.../versions/.../diff/...`` works (M1.2).

Replaces the old 501-stub assertion. After M1.2 the route resolves
``a`` / ``b`` through :meth:`SkillPackVersionRepository.get_by_label`
(``"active"`` / ``"latest"`` / numeric / UUID) and renders a real
unified diff.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"vd-{uuid.uuid4().hex[:8]}@example.com"
    password = "diff-versions-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Diff V Tester", "password": password},
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


async def _create_pack_with_two_versions(async_client, headers) -> str:
    payload = {
        "slug": f"vd-{uuid.uuid4().hex[:8]}",
        "name": "Versioned",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": "alpha\nbeta\n",
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "alpha\nGAMMA\n"},
    )
    assert r.status_code == 200, r.text
    return pid


async def test_diff_two_versions_by_number(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack_with_two_versions(async_client, headers)
    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/versions/1/diff/2", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is False
    assert body["stats"]["added_lines"] == 1
    assert body["stats"]["removed_lines"] == 1
    assert "+GAMMA" in body["diff"]
    assert "-beta" in body["diff"]


async def test_diff_with_active_label(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack_with_two_versions(async_client, headers)
    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/versions/1/diff/active", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "+GAMMA" in body["diff"]


async def test_diff_with_latest_label(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack_with_two_versions(async_client, headers)
    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/versions/active/diff/latest", headers=headers
    )
    assert r.status_code == 200, r.text


async def test_diff_unknown_version_returns_404_not_501(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack_with_two_versions(async_client, headers)
    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/versions/1/diff/99", headers=headers
    )
    assert r.status_code == 404
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "skill_version.not_found"


async def test_diff_unknown_pack_returns_404(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    bogus = uuid.uuid4()
    r = await async_client.get(
        f"/api/v1/skills/packs/{bogus}/versions/1/diff/2", headers=headers
    )
    assert r.status_code == 404
