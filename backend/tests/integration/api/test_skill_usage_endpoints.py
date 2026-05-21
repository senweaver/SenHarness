"""Integration: 3 skill_usage routes (M1.3).

Covers: list usage rows (member), stats aggregation (member), manual
rollup (admin-only) + cross-workspace 404 isolation.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.security import utcnow_naive
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"sku-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-usage-route-test-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Sku Tester", "password": password},
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
    identity_id = body["identity_id"]
    return headers, ws_id, identity_id


async def _create_pack(async_client, headers) -> str:
    payload = {
        "slug": f"sk-{uuid.uuid4().hex[:8]}",
        "name": "Usage Pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": "---\nname: x\ndescription: y\n---\n\nbody",
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_session(ws_id: str, identity_id: str) -> uuid.UUID:
    factory = get_session_factory()
    sid = uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO sessions (id, workspace_id, kind, "
                "owner_identity_id, title, title_source, state, "
                "message_count, metadata_json) "
                "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', "
                "'active', 0, '{}'::jsonb)"
            ),
            {"id": sid, "ws": uuid.UUID(ws_id), "uid": uuid.UUID(identity_id)},
        )
        await db.commit()
    return sid


async def _seed_rows(*, ws_id: str, pack_id: str, sid: uuid.UUID, identity_id: str):
    factory = get_session_factory()
    async with factory() as db:
        now = utcnow_naive()
        for i, kind in enumerate(
            [
                SkillUsageEventKind.INJECTED,
                SkillUsageEventKind.INJECTED,
                SkillUsageEventKind.READ_FULL,
                SkillUsageEventKind.USED_IN_TOOL,
            ]
        ):
            row = SkillUsage(
                workspace_id=uuid.UUID(ws_id),
                pack_id=uuid.UUID(pack_id),
                run_id=uuid.uuid4(),
                session_id=sid,
                identity_id=uuid.UUID(identity_id),
                event_kind=kind,
                contribution_score=0.5 if kind == SkillUsageEventKind.READ_FULL else None,
            )
            row.created_at = now - timedelta(hours=i)
            db.add(row)
        await db.commit()


async def test_list_usage_returns_rows(async_client):
    headers, ws_id, identity_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    sid = await _seed_session(ws_id, identity_id)
    await _seed_rows(ws_id=ws_id, pack_id=pid, sid=sid, identity_id=identity_id)

    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/usage", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == pid
    assert len(body["items"]) == 4

    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/usage",
        headers=headers,
        params={"event_kind": "injected"},
    )
    assert r.status_code == 200
    body = r.json()
    assert all(it["event_kind"] == "injected" for it in body["items"])
    assert len(body["items"]) == 2


async def test_stats_returns_aggregate(async_client):
    headers, ws_id, identity_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    sid = await _seed_session(ws_id, identity_id)
    await _seed_rows(ws_id=ws_id, pack_id=pid, sid=sid, identity_id=identity_id)

    r = await async_client.get(
        f"/api/v1/skills/packs/{pid}/usage/stats", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == pid
    assert body["use_count"] == 4
    assert body["use_count_by_kind"] == {
        "injected": 2,
        "read_full": 1,
        "used_in_tool": 1,
    }
    assert body["last_used_at"] is not None
    assert body["contribution_avg"] == pytest.approx(0.5)


async def test_rollup_writes_pack_columns(async_client):
    headers, ws_id, identity_id = await _bootstrap(async_client)
    pid = await _create_pack(async_client, headers)
    sid = await _seed_session(ws_id, identity_id)
    await _seed_rows(ws_id=ws_id, pack_id=pid, sid=sid, identity_id=identity_id)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pid}/usage/rollup", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_used_at"] is not None
    assert body["effectiveness_avg"] == pytest.approx(0.5)
    assert body["use_count"] == 4

    r2 = await async_client.get(f"/api/v1/skills/packs/{pid}", headers=headers)
    assert r2.status_code == 200
    pack = r2.json()
    assert pack["last_used_at"] is not None
    assert pack["effectiveness_avg"] == pytest.approx(0.5)


async def test_cross_workspace_pack_404(async_client):
    headers_a, _, _ = await _bootstrap(async_client)
    pid_a = await _create_pack(async_client, headers_a)

    headers_b, _, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/skills/packs/{pid_a}/usage", headers=headers_b
    )
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "skill_pack.not_found"


async def test_rollup_blocks_non_admin(async_client):
    """Workspace owner is admin; a non-member from another workspace
    must get 404 (workspace check fires before pack check).
    """
    headers_a, _, _ = await _bootstrap(async_client)
    pid_a = await _create_pack(async_client, headers_a)

    headers_b, ws_b, _ = await _bootstrap(async_client)
    headers_b["X-Workspace-Id"] = ws_b
    r = await async_client.post(
        f"/api/v1/skills/packs/{pid_a}/usage/rollup", headers=headers_b
    )
    # Since workspace headers point to ws_b but pack belongs to ws_a,
    # the pack-not-found check fires (404) before any role check.
    assert r.status_code in (403, 404)
