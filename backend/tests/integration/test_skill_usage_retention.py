"""Integration: M0.11 retention cascade includes ``skill_usage`` (M1.3).

The ``CASCADE_TARGETS`` whitelist gained a ``skill_usage`` entry as
part of M1.3. This test seeds usage rows, soft-deletes the owning
identity, runs the retention sweep, and asserts the rows are gone
(physical delete because ``soft_delete=False``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack, SkillPackSource
from app.db.session import get_session_factory
from app.jobs.retention import retention_sweep_cascade

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[str, str, str]:
    email = f"sku-rt-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-usage-retention-test-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "SKU Ret", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return body["identity_id"], workspace.get("id"), email


async def _seed_pack(ws_id: str) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"sk-{uuid.uuid4().hex[:8]}",
            name="Cascade Pack",
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


async def _seed_usage_for_identity(
    *, ws_id: str, identity_id: str, pack_id: uuid.UUID, sid: uuid.UUID, n: int = 3
) -> list[uuid.UUID]:
    factory = get_session_factory()
    ids: list[uuid.UUID] = []
    async with factory() as db:
        for _ in range(n):
            row = SkillUsage(
                workspace_id=uuid.UUID(ws_id),
                pack_id=pack_id,
                run_id=uuid.uuid4(),
                session_id=sid,
                identity_id=uuid.UUID(identity_id),
                event_kind=SkillUsageEventKind.READ_FULL,
            )
            db.add(row)
            await db.flush([row])
            ids.append(row.id)
        await db.commit()
    return ids


async def _soft_delete_identity(ident_id: str) -> datetime:
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text("UPDATE identities SET deleted_at = now() WHERE id = :id"),
            {"id": uuid.UUID(ident_id)},
        )
        row = (
            await db.execute(
                text("SELECT deleted_at FROM identities WHERE id = :id"),
                {"id": uuid.UUID(ident_id)},
            )
        ).one()
        await db.commit()
        return row[0]


async def _reset_watermarks_to_far_past() -> None:
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE retention_watermarks "
                "SET last_seen_deleted_at = now() - interval '1 hour'"
            )
        )
        await db.commit()


async def test_skill_usage_cascade_on_identity_soft_delete(async_client):
    identity_id, ws_id, _ = await _bootstrap(async_client)
    pid = await _seed_pack(ws_id)
    sid = await _seed_session(ws_id, identity_id)
    seeded = await _seed_usage_for_identity(
        ws_id=ws_id, identity_id=identity_id, pack_id=pid, sid=sid, n=3
    )

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(SkillUsage).where(SkillUsage.id.in_(seeded))
            )
        ).scalars().all()
        assert len(rows) == 3

    await _soft_delete_identity(identity_id)
    await _reset_watermarks_to_far_past()

    summary = await retention_sweep_cascade({})
    assert summary["identities_swept"] >= 1

    async with factory() as db:
        remaining = (
            await db.execute(
                select(SkillUsage).where(SkillUsage.id.in_(seeded))
            )
        ).scalars().all()
        # ``soft_delete=False`` → physical delete.
        assert len(remaining) == 0
