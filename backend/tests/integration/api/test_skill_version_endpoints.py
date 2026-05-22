"""Integration: M1.2 SkillPackVersion routes.

Each route gets one happy path; RBAC + cross-workspace isolation are
covered alongside.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"sv-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-version-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "SV Tester", "password": password},
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


async def _create_pack(async_client, headers, *, body="initial body") -> str:
    payload = {
        "slug": f"sv-{uuid.uuid4().hex[:8]}",
        "name": "SV pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": body,
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_create_pack_seeds_v1_and_list_returns_it(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="hello v1 world")

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == pid
    assert len(body["items"]) == 1
    only = body["items"][0]
    assert only["version_no"] == 1
    assert only["state"] == "active"


async def test_active_version_endpoint_returns_content(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="content for active")

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions/active", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "active"
    assert body["content_md"] == "content for active"


async def test_get_version_by_number(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions/1", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_no"] == 1


async def test_unknown_version_no_returns_404(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions/99", headers=headers)
    assert r.status_code == 404


async def test_update_pack_creates_v2_and_activates(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="v1 body original")

    r = await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "v2 body rewritten by user"},
    )
    assert r.status_code == 200, r.text
    pack_after = r.json()

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    versions = r.json()["items"]
    assert {v["version_no"] for v in versions} == {1, 2}
    states = {v["version_no"]: v["state"] for v in versions}
    assert states[1] == "retired"
    assert states[2] == "active"

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/content", headers=headers)
    assert r.json()["content_md"] == "v2 body rewritten by user"

    assert pack_after["content_hash"] is not None


async def test_update_pack_with_identical_body_does_not_create_duplicate(
    async_client,
) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="same forever")
    r = await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "same forever"},
    )
    assert r.status_code == 200
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    assert len(r.json()["items"]) == 1


async def test_activate_version_endpoint_swaps_active(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="v1 body")
    await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "v2 body"},
    )
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    versions = r.json()["items"]
    v1 = next(v for v in versions if v["version_no"] == 1)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/activate",
        headers=headers,
        json={"reason": "rollback to v1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions/active", headers=headers)
    assert r.json()["version_no"] == 1
    assert r.json()["content_md"] == "v1 body"


async def test_transition_endpoint_drives_state_machine(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "intermediate body"},
    )
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    v_active = next(v for v in r.json()["items"] if v["state"] == "active")
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v_active['id']}/transition",
        headers=headers,
        json={"target_state": "retired", "reason": "manual retire"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "retired"


async def test_cross_workspace_version_lookup_returns_404(async_client) -> None:
    headers_a, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers_a)
    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers_b)
    assert r.status_code == 404


async def test_admin_only_for_activate_route(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    await async_client.patch(
        f"/api/v1/skills/packs/{pid}",
        headers=headers,
        json={"content_md": "v2 alt"},
    )
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    target = next(v for v in r.json()["items"] if v["version_no"] == 1)

    inv = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        headers=headers,
        json={"role": "member"},
    )
    if inv.status_code != 201:
        pytest.skip(f"invitation creation unavailable: {inv.text}")
    code = inv.json()["code"]
    headers_member, _ = await _bootstrap(async_client)
    accept = await async_client.post(
        "/api/v1/workspaces/invitations/accept",
        headers=headers_member,
        json={"code": code},
    )
    if accept.status_code not in (200, 201):
        pytest.skip(f"invitation accept unavailable: {accept.text}")
    headers_member["X-Workspace-Id"] = ws_id

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{target['id']}/activate",
        headers=headers_member,
        json={"reason": "non-admin"},
    )
    assert r.status_code == 403


async def test_invalid_transition_returns_409(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    r = await async_client.get(f"/api/v1/skills/packs/{pid}/versions", headers=headers)
    v1 = r.json()["items"][0]
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/transition",
        headers=headers,
        json={"target_state": "validating", "reason": "bad edge"},
    )
    assert r.status_code == 409
    detail = r.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else r.json().get("code")
    assert code == "skill_version.invalid_transition"
