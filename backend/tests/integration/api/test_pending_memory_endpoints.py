"""End-to-end tests for the M0.7 pending-memory REST routes.

Covers happy paths + RBAC across the four endpoints + cross-workspace
isolation. The platform-admin debug ``promote-now`` endpoint is
exercised by promoting an admin identity inline.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"pm-api-{uuid.uuid4().hex[:8]}@example.com"
    password = "pending-memory-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Pm Api", "password": password},
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
        json={"name": "PM WS", "slug": f"pm-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _new_session(async_client, headers) -> str:
    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def _seed_pending(
    *, workspace_id: str, session_id: str, identity_id: str
) -> str:
    from app.db.models.pending_memory import PendingMemoryTargetTable
    from app.db.session import get_session_factory
    from app.services import pending_memory as pending_memory_svc

    factory = get_session_factory()
    async with factory() as db:
        row, _ = await pending_memory_svc.queue_immediate_or_pending(
            db,
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            identity_id=uuid.UUID(identity_id),
            agent_id=None,
            target_table=PendingMemoryTargetTable.MEMORIES,
            payload={
                "content": "fact-seed",
                "scope": "user",
                "kind": "semantic",
            },
        )
        await db.commit()
        assert row is not None
        return str(row.id)


async def _promote_to_platform_admin(identity_id: str) -> None:
    from app.db.models.identity import Identity, PlatformRole
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        ident = await db.get(Identity, uuid.UUID(identity_id))
        assert ident is not None
        ident.platform_role = PlatformRole.ADMIN
        await db.commit()


async def test_list_session_pending_happy(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    iid = _identity_id_from_token(headers)
    pid = await _seed_pending(
        workspace_id=ws_id, session_id=sid, identity_id=iid
    )
    r = await async_client.get(
        f"/api/v1/sessions/{sid}/pending-memories", headers=headers
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert any(row["id"] == pid for row in rows)


async def test_list_session_pending_cross_workspace_blocked(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers_a)
    iid_a = _identity_id_from_token(headers_a)
    await _seed_pending(workspace_id=ws_a, session_id=sid, identity_id=iid_a)

    headers_b, _ws_b = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/sessions/{sid}/pending-memories", headers=headers_b
    )
    assert r.status_code in (403, 404)


async def test_cancel_pending_happy(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    iid = _identity_id_from_token(headers)
    pid = await _seed_pending(
        workspace_id=ws_id, session_id=sid, identity_id=iid
    )
    r = await async_client.post(
        f"/api/v1/sessions/{sid}/pending-memories/{pid}/cancel",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "skipped"
    assert r.json()["failure_reason"] == "user_cancelled"


async def test_workspace_stats_admin_only(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    iid = _identity_id_from_token(headers)
    await _seed_pending(workspace_id=ws_id, session_id=sid, identity_id=iid)

    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/pending-memories/stats",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] >= 1


async def test_workspace_stats_cross_tenant_blocked(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    headers_b, _ws_b = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/workspaces/{ws_a}/pending-memories/stats",
        headers=headers_b,
    )
    assert r.status_code == 404


async def test_admin_promote_now_requires_platform_admin(async_client):
    headers, _ws_id = await _bootstrap(async_client)
    r = await async_client.post(
        "/api/v1/admin/pending-memories/promote-now", headers=headers
    )
    assert r.status_code in (401, 403)


async def test_admin_promote_now_happy(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    iid = _identity_id_from_token(headers)
    await _seed_pending(workspace_id=ws_id, session_id=sid, identity_id=iid)
    await _promote_to_platform_admin(iid)

    r = await async_client.post(
        "/api/v1/admin/pending-memories/promote-now", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "workspaces_visited" in body
    assert "promoted" in body
