"""Integration: M1.6 rollback verb endpoint.

Bridges the M1.2 service-layer ``rollback_to_version`` to a wire
verb. Each test exercises one branch of the contract: happy path
(state + content mirror), audit emission, RBAC, cross-workspace
isolation, idempotent NoOp on the live ACTIVE row, REJECTED → 409,
and rate-limit enforcement.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    """Register a fresh identity + capture its personal workspace.

    Each invocation pins a unique ``X-Forwarded-For`` token so the
    Redis-backed ``skill_version_rollback`` bucket is isolated
    per-test. Without it the IP-based fallback identifier shares one
    bucket across the whole test session and tail tests start past
    the 10/60s cap, producing spurious 429s before the cap-exhaustion
    test even runs.
    """
    email = f"sr-{uuid.uuid4().hex[:8]}@example.com"
    password = "rollback-tester-very-long-password"
    forwarded_for = f"10.0.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Rollback Tester", "password": password},
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
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Forwarded-For": forwarded_for,
    }
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _create_pack(async_client, headers, *, body="initial body") -> str:
    payload = {
        "slug": f"sr-{uuid.uuid4().hex[:8]}",
        "name": "Rollback pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": body,
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _patch_body(async_client, headers, pack_id: str, body: str) -> None:
    r = await async_client.patch(
        f"/api/v1/skills/packs/{pack_id}",
        headers=headers,
        json={"content_md": body},
    )
    assert r.status_code == 200, r.text


async def _versions(async_client, headers, pack_id: str) -> list[dict]:
    r = await async_client.get(f"/api/v1/skills/packs/{pack_id}/versions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_rollback_promotes_v1_and_retires_current_active(
    async_client,
) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="v1 body")
    await _patch_body(async_client, headers, pid, "v2 body")
    await _patch_body(async_client, headers, pid, "v3 body")

    versions = await _versions(async_client, headers, pid)
    by_no = {v["version_no"]: v for v in versions}
    v1 = by_no[1]

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={"reason": "v3 broke something subtle"},
    )
    assert r.status_code == 200, r.text
    rolled = r.json()
    assert rolled["version_no"] == 1
    assert rolled["state"] == "active"

    versions = await _versions(async_client, headers, pid)
    by_no = {v["version_no"]: v for v in versions}
    assert by_no[1]["state"] == "active"
    assert by_no[2]["state"] == "retired"
    assert by_no[3]["state"] == "retired"

    r = await async_client.get(f"/api/v1/skills/packs/{pid}/content", headers=headers)
    assert r.json()["content_md"] == "v1 body"


async def test_rollback_writes_dedicated_audit_row(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="initial")
    await _patch_body(async_client, headers, pid, "second")

    versions = await _versions(async_client, headers, pid)
    v1 = next(v for v in versions if v["version_no"] == 1)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={"reason": "rollback audit smoke test"},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                        AuditEvent.action == "skill_version.rollback",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 1
    audit = rows[-1]
    assert audit.metadata_json["target_version_no"] == 1
    assert audit.metadata_json["reason"] == "rollback audit smoke test"
    assert audit.metadata_json["pack_id"] == pid


async def test_rollback_requires_admin_returns_403(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="admin gated")
    await _patch_body(async_client, headers, pid, "second body")

    versions = await _versions(async_client, headers, pid)
    v1 = next(v for v in versions if v["version_no"] == 1)

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
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers_member,
        json={"reason": "non-admin attempt"},
    )
    assert r.status_code == 403


async def test_rollback_cross_workspace_returns_404(async_client) -> None:
    headers_a, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers_a, body="origin")
    await _patch_body(async_client, headers_a, pid, "second")
    versions = await _versions(async_client, headers_a, pid)
    v1 = next(v for v in versions if v["version_no"] == 1)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers_b,
        json={"reason": "leak attempt"},
    )
    assert r.status_code == 404


async def test_rollback_to_currently_active_is_idempotent(async_client) -> None:
    """Rolling back to the row that's already ACTIVE returns 200 with
    no state change — service short-circuits the retire side via
    ``current.id == superseded_by.id``.
    """
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="only one")

    versions = await _versions(async_client, headers, pid)
    v1 = versions[0]
    assert v1["state"] == "active"

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={"reason": "noop self-rollback"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"

    versions = await _versions(async_client, headers, pid)
    assert {v["version_no"] for v in versions} == {1}
    assert versions[0]["state"] == "active"


async def test_rollback_to_rejected_version_returns_409(async_client) -> None:
    """Once a version is REJECTED the service refuses to revive it —
    rollback bubbles ``skill_version.invalid_transition`` (409).
    """
    headers, ws_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="root version")

    # Drop a fresh PROPOSED row directly through the service so we can
    # transition it to REJECTED without disturbing the ACTIVE pointer
    # (the public PATCH path always activates after creating).
    from app.db.models.skill_pack_version import SkillPackVersionState
    from app.db.session import get_session_factory
    from app.services import skill_version as svc

    factory = get_session_factory()
    async with factory() as db:
        proposed = await svc.create_version(
            db,
            workspace_id=uuid.UUID(ws_id),
            pack_id=uuid.UUID(pid),
            content_md="never made it to prod",
            files=None,
            created_by="user",
            creator_identity_id=None,
        )
        await svc.transition_version(
            db,
            workspace_id=uuid.UUID(ws_id),
            version_id=proposed.id,
            target_state=SkillPackVersionState.REJECTED,
            actor_identity_id=None,
            reason="killed by reviewer",
        )
        await db.commit()
        rejected_id = str(proposed.id)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{rejected_id}/rollback",
        headers=headers,
        json={"reason": "trying to revive a rejected version"},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "skill_version.invalid_transition"


async def test_rollback_rate_limit_returns_429(async_client) -> None:
    """``skill_version_rollback`` bucket is 10/60s. The 11th call in
    a fresh window must surface 429 with the standard
    ``rate_limit.exceeded`` code.
    """
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="rate limit subject")
    versions = await _versions(async_client, headers, pid)
    v1 = versions[0]

    last = None
    for _ in range(12):
        last = await async_client.post(
            f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
            headers=headers,
            json={"reason": "rate flood"},
        )
        if last.status_code == 429:
            break
    assert last is not None
    assert last.status_code == 429, last.text
    body = last.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "rate_limit.exceeded"


async def test_rollback_validates_reason_required(async_client) -> None:
    headers, _ = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers, body="reason required")
    await _patch_body(async_client, headers, pid, "second")
    versions = await _versions(async_client, headers, pid)
    v1 = next(v for v in versions if v["version_no"] == 1)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={},
    )
    assert r.status_code == 422

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={"reason": ""},
    )
    assert r.status_code == 422

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/versions/{v1['id']}/rollback",
        headers=headers,
        json={"reason": "x" * 401},
    )
    assert r.status_code == 422
