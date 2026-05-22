"""Integration: ``rollup_skill_usage`` ARQ job (M1.3).

Seeds usage rows for one pack per workspace and asserts the rollup
writes back ``last_used_at`` + ``effectiveness_avg`` on the matching
``SkillPack`` rows. A second workspace-scoped pack confirms rollup
isolation across tenants.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack, SkillPackSource
from app.db.session import get_session_factory
from app.jobs.skill_telemetry import rollup_skill_usage

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"sktel-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-telemetry-test-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Skill Tel", "password": password},
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


async def _seed_pack(ws_id: str) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"sk-{uuid.uuid4().hex[:8]}",
            name="Rollup Pack",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
        )
        db.add(pack)
        await db.flush([pack])
        pid = pack.id
        await db.commit()
    return pid


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


async def _seed_usage_rows(
    *, ws_id: str, pack_id: uuid.UUID, sid: uuid.UUID, identity_id: str
) -> None:
    factory = get_session_factory()
    now = utcnow_naive()
    async with factory() as db:
        for i, score in enumerate([0.5, 0.7, 0.9]):
            row = SkillUsage(
                workspace_id=uuid.UUID(ws_id),
                pack_id=pack_id,
                run_id=uuid.uuid4(),
                session_id=sid,
                identity_id=uuid.UUID(identity_id),
                event_kind=SkillUsageEventKind.READ_FULL,
                contribution_score=score,
            )
            row.created_at = now - timedelta(days=i + 1)
            db.add(row)
        # An older row for the by_kind variety check.
        old = SkillUsage(
            workspace_id=uuid.UUID(ws_id),
            pack_id=pack_id,
            run_id=uuid.uuid4(),
            session_id=sid,
            identity_id=uuid.UUID(identity_id),
            event_kind=SkillUsageEventKind.INJECTED,
            contribution_score=None,
        )
        old.created_at = now - timedelta(days=2)
        db.add(old)
        await db.commit()


async def _force_pack_stale(pack_id: uuid.UUID) -> None:
    """The rollup skips packs whose last_used_at is fresher than 24h.
    Force the stored value to ``NULL`` so the rollup considers it.
    """
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text("UPDATE skill_packs SET last_used_at = NULL WHERE id = :id"),
            {"id": pack_id},
        )
        await db.commit()


async def test_rollup_writes_last_used_at_and_effectiveness(async_client):
    _headers, ws_id, identity_id = await _bootstrap(async_client)
    sid = await _seed_session(ws_id, identity_id)
    pid = await _seed_pack(ws_id)
    await _seed_usage_rows(ws_id=ws_id, pack_id=pid, sid=sid, identity_id=identity_id)
    await _force_pack_stale(pid)

    summary = await rollup_skill_usage({})

    assert summary["status"] == "ok"
    assert summary["workspaces_processed"] >= 1
    assert summary["packs_updated"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        pack = (await db.execute(select(SkillPack).where(SkillPack.id == pid))).scalar_one()
        assert pack.last_used_at is not None
        assert pack.effectiveness_avg == pytest.approx(0.7, rel=1e-3)

        audits = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == "skill.stats_rolled_up",
                        AuditEvent.resource_id == pid,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) >= 1


async def test_rollup_isolates_workspaces(async_client):
    _headers_a, ws_a, ident_a = await _bootstrap(async_client)
    sid_a = await _seed_session(ws_a, ident_a)
    pid_a = await _seed_pack(ws_a)
    await _seed_usage_rows(ws_id=ws_a, pack_id=pid_a, sid=sid_a, identity_id=ident_a)
    await _force_pack_stale(pid_a)

    _headers_b, ws_b, _ident_b = await _bootstrap(async_client)
    pid_b = await _seed_pack(ws_b)
    # No usage rows seeded for ws_b's pack — rollup must leave it
    # untouched (use_count == 0 path).
    await _force_pack_stale(pid_b)

    await rollup_skill_usage({})

    factory = get_session_factory()
    async with factory() as db:
        pack_a = (await db.execute(select(SkillPack).where(SkillPack.id == pid_a))).scalar_one()
        assert pack_a.last_used_at is not None
        pack_b = (await db.execute(select(SkillPack).where(SkillPack.id == pid_b))).scalar_one()
        assert pack_b.last_used_at is None
        assert pack_b.effectiveness_avg is None
