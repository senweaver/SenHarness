"""Integration: ``reap_zombies`` end-to-end (M2.5.1).

Seeds 5 RUNNING ``subagent_runs`` rows — 3 with stale heartbeats and
2 fresh — then runs the cron and asserts the 3 stale rows landed in
``ZOMBIE`` while the 2 fresh ones stay RUNNING. Also verifies the
``subagent.zombie_reaped`` audit lands and the M0.10
``subagent.zombie_detected`` notification fan-out fires.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.db.session import get_session_factory
from app.jobs.subagent_zombie import reap_zombies

pytestmark = pytest.mark.asyncio


async def _make_workspace(async_client) -> str:
    email = f"reaper-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Reaper Tester",
            "password": "reap-test-password-very-long",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["workspace"]["id"]


async def _seed_run(
    *,
    ws_id: str,
    heartbeat_age_seconds: int,
    retry_count: int = 0,
) -> uuid.UUID:
    factory = get_session_factory()
    now = utcnow_naive()
    async with factory() as db:
        row = SubAgentRun(
            workspace_id=uuid.UUID(ws_id),
            parent_run_id=uuid.uuid4(),
            child_run_id=uuid.uuid4(),
            spawn_depth=0,
            state=SubAgentRunState.RUNNING,
            last_heartbeat_at=now - timedelta(seconds=heartbeat_age_seconds),
            retry_count=retry_count,
            retry_budget=3,
        )
        db.add(row)
        await db.flush([row])
        spine_id = row.id
        await db.commit()
    return spine_id


async def test_reap_zombies_marks_stale_rows_only(async_client):
    ws_id = await _make_workspace(async_client)

    stale_ids = [
        await _seed_run(ws_id=ws_id, heartbeat_age_seconds=600, retry_count=1),
        await _seed_run(ws_id=ws_id, heartbeat_age_seconds=900, retry_count=2),
        await _seed_run(ws_id=ws_id, heartbeat_age_seconds=320),
    ]
    fresh_ids = [
        await _seed_run(ws_id=ws_id, heartbeat_age_seconds=10),
        await _seed_run(ws_id=ws_id, heartbeat_age_seconds=120),
    ]

    summary = await reap_zombies({})
    assert summary["status"] == "ok"
    assert summary["stale_seen"] >= 3
    assert summary["reaped"] >= 3
    # Two of the stale rows had retry_count > 0, so the budget refund
    # should fire at least twice.
    assert summary["budget_refunded"] >= 2

    factory = get_session_factory()
    async with factory() as db:
        for spine_id in stale_ids:
            row = await db.get(SubAgentRun, spine_id)
            assert row is not None
            assert row.state == SubAgentRunState.ZOMBIE
            assert row.error_kind == "heartbeat_lost"
        for spine_id in fresh_ids:
            row = await db.get(SubAgentRun, spine_id)
            assert row is not None
            assert row.state == SubAgentRunState.RUNNING

        audit = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "subagent.zombie_reaped")
                .where(AuditEvent.workspace_id == uuid.UUID(ws_id))
            )
        ).scalars().all()
        assert len(audit) >= 3

        notif = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "notification.emitted")
                .where(AuditEvent.workspace_id == uuid.UUID(ws_id))
            )
        ).scalars().all()
        # M0.10 fan-out writes one ``notification.emitted`` per emit;
        # at minimum 3 zombie events should produce >= 3 emit rows
        # (cooldown_resource_id is the spine row id so siblings don't
        # dedup).
        assert len(notif) >= 3


async def test_reap_zombies_idempotent_on_double_tick(async_client):
    ws_id = await _make_workspace(async_client)
    await _seed_run(ws_id=ws_id, heartbeat_age_seconds=600)

    first = await reap_zombies({})
    second = await reap_zombies({})

    assert first["reaped"] >= 1
    # Second tick should see no new stale rows (the ones from the
    # first tick are now in ZOMBIE state so list_stale skips them).
    assert second["reaped"] == 0
