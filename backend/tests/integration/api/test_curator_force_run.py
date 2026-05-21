"""Integration: ``trigger_curator_now`` synchronous helper (M1.4 + M1.9).

The M1.9 admin "Force run curator now" button enqueues a one-shot
``trigger_curator_now`` call. M1.4 ships the service entry point so
M1.9 only has to wire the route + RBAC; this test exercises the
service path against a real DB so M1.9 can rely on the contract.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalResourceType
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.db.session import get_session_factory
from app.services import skill_curator as svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> str:
    email = f"force-{uuid.uuid4().hex[:8]}@example.com"
    password = "force-curator-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Force Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return workspace["id"]


async def test_trigger_curator_now_returns_workspace_summary(async_client):
    ws_id = await _bootstrap(async_client)
    now = utcnow_naive()

    factory = get_session_factory()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"force-{uuid.uuid4().hex[:8]}",
            name="Force Pack",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=SkillPackState.STALE,
        )
        pack.last_used_at = now - timedelta(days=120)
        pack.state_changed_at = now - timedelta(days=120)
        db.add(pack)
        await db.flush([pack])
        pid = pack.id
        await db.commit()

    result = await svc.trigger_curator_now(workspace_id=uuid.UUID(ws_id))

    assert result["status"] == "ok"
    assert result["workspace_id"] == ws_id
    assert result["archive_proposed"] >= 1
    # No ACTIVE packs were seeded; stale_transitioned must be 0.
    assert result["stale_transitioned"] == 0

    async with factory() as db:
        approvals = (
            await db.execute(
                select(Approval).where(
                    Approval.workspace_id == uuid.UUID(ws_id),
                    Approval.resource_type
                    == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                    Approval.resource_id == pid,
                )
            )
        ).scalars().all()
        assert len(approvals) == 1


async def test_trigger_curator_now_disabled_workspace_short_circuits(async_client):
    ws_id = await _bootstrap(async_client)

    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import text

        await db.execute(
            text(
                "UPDATE workspaces "
                "SET home_config_json = jsonb_set("
                "  COALESCE(home_config_json, '{}'::jsonb), "
                "  '{curator}', "
                "  '{\"enabled\": false}'::jsonb"
                ") "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(ws_id)},
        )
        await db.commit()

    result = await svc.trigger_curator_now(workspace_id=uuid.UUID(ws_id))
    assert result["status"] == "disabled"
    assert result["workspace_id"] == ws_id
