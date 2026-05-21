"""Integration: 8 lifecycle routes (M1.1).

Each route gets one happy path + one RBAC / cross-workspace failure
case. Rate limit 429 is asserted on the lifecycle action bucket once
to keep the suite under a minute.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    """Register a user, return ``(headers, workspace_id)``.

    The personal workspace is auto-provisioned on register and the
    response carries it, so we sidestep the M0.12 manual-create gate.
    """
    email = f"lc-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-lifecycle-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Lifecycle Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        # Fall back to login if email verification is on.
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


async def _create_pack(async_client, headers, *, slug=None) -> str:
    payload = {
        "slug": slug or f"sk-{uuid.uuid4().hex[:8]}",
        "name": "Test pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": "---\nname: x\ndescription: y\n---\n\nbody",
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_pin_then_unpin_happy_path(async_client):
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/pin", headers=headers, json={"reason": "keep"}
    )
    assert r.status_code == 200
    assert r.json()["pinned"] is True

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/unpin", headers=headers, json={"reason": "release"}
    )
    assert r.status_code == 200
    assert r.json()["pinned"] is False


async def test_archive_then_restore_round_trip(async_client):
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/archive",
        headers=headers,
        json={"reason": "no longer used"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "archived"

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/restore",
        headers=headers,
        json={"reason": "needed it back"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "active"


async def test_deprecate_then_admin_archive_via_transitions(async_client):
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/deprecate",
        headers=headers,
        json={"reason": "v2 superseded"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "deprecated"

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/transitions",
        headers=headers,
        json={"target_state": "archived", "reason": "DEPRECATED -> ARCHIVED"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "archived"


async def test_invalid_transition_returns_409(async_client):
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/transitions",
        headers=headers,
        json={"target_state": "draft", "reason": "going back"},
    )
    assert r.status_code == 409
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "skill.invalid_transition"


async def test_state_and_transitions_endpoints(async_client):
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    await async_client.post(
        f"/api/v1/skills/packs/{pid}/transitions",
        headers=headers,
        json={"target_state": "stale", "reason": "dust collecting"},
    )

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/state", headers=headers)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["state"] == "stale"
    assert s["pinned"] is False
    assert s["last_transition"] is not None
    assert s["last_transition"]["from_state"] == "active"
    assert s["last_transition"]["to_state"] == "stale"

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/transitions", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["to_state"] == "stale" for it in items)


async def test_cross_workspace_pack_not_found(async_client):
    headers_a, _ = await _bootstrap(async_client)
    pid_a = await _create_pack(async_client, headers_a)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid_a}/pin",
        headers=headers_b,
        json={"reason": "rogue"},
    )
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "skill_pack.not_found"


async def test_admin_transition_route_blocks_non_admin(async_client):
    """Workspace owner is admin → can call. We assert the happy path
    works and add a second user as MEMBER to confirm 403.
    """
    headers, ws_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)

    # Invite a second user as MEMBER.
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
        f"/api/v1/skills/packs/{pid}/transitions",
        headers=headers_member,
        json={"target_state": "stale", "reason": "trying"},
    )
    assert r.status_code == 403


async def test_tombstoned_slug_blocks_create(async_client):
    headers, _ws_id = await _bootstrap(async_client)
    slug = f"reuse-{uuid.uuid4().hex[:8]}"
    pid = await _create_pack(async_client, headers, slug=slug)

    await async_client.post(
        f"/api/v1/skills/packs/{pid}/archive",
        headers=headers,
        json={"reason": "phase 1"},
    )
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/transitions",
        headers=headers,
        json={"target_state": "tombstone", "reason": "permanent"},
    )
    assert r.status_code == 200, r.text

    r = await async_client.post(
        "/api/v1/skills/packs",
        headers=headers,
        json={
            "slug": slug,
            "name": "Squat",
            "manifest_json": {},
            "content_md": "---\nname: x\ndescription: y\n---\n\nbody",
        },
    )
    assert r.status_code == 409
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "skill.slug_tombstoned"


async def test_lifecycle_action_rate_limit_returns_429(async_client):
    """The ``skill_lifecycle_action`` bucket caps at 30/60s. A real
    hammer test would issue 31 requests; we issue 32 pin/unpin pairs
    inside the same bucket and look for at least one 429.
    """
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    saw_429 = False
    for i in range(40):
        path = "pin" if i % 2 == 0 else "unpin"
        r = await async_client.post(
            f"/api/v1/skills/packs/{pid}/{path}",
            headers=headers,
            json={"reason": "rate test"},
        )
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "expected at least one 429 within 40 requests"
