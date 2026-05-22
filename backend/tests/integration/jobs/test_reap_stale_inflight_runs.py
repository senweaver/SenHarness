"""Integration: M2.5.2 ``reap_stale_inflight_runs`` end-to-end.

Seeds RUNNING ``inflight_runs`` with stale + fresh ``last_seen_at``,
runs the cron, and asserts the stale rows landed in ``LOST`` with the
``inflight_run.timed_out_to_lost`` audit + ``inflight_run.lost_detected``
notification fan-out. Also verifies the same-host live-PID spare path.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.session import get_session_factory
from app.jobs.inflight_recovery import reap_stale_inflight_runs

pytestmark = pytest.mark.asyncio


async def _make_workspace(async_client) -> tuple[str, str]:
    """Returns (workspace_id, identity_id) of a freshly registered tester."""
    email = f"inflight-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Inflight Tester",
            "password": "inflight-test-password-very-long",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["workspace"]["id"], body["identity"]["id"]


async def _seed_run(
    *,
    ws_id: str,
    identity_id: str,
    last_seen_age_seconds: int,
    pid_token: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    factory = get_session_factory()
    now = utcnow_naive()
    async with factory() as db:
        from app.repositories.session import SessionRepository

        sess = await SessionRepository(db).create(
            workspace_id=uuid.UUID(ws_id),
            owner_identity_id=uuid.UUID(identity_id),
            title=f"inflight-test-{uuid.uuid4().hex[:6]}",
        )
        run_id = uuid.uuid4()
        row = InflightRun(
            workspace_id=uuid.UUID(ws_id),
            run_id=run_id,
            session_id=sess.id,
            identity_id=uuid.UUID(identity_id),
            backend_kind="native",
            request_snapshot={"trigger": "test"},
            state=InflightRunState.RUNNING,
            pid_token=pid_token or "previous-host:1234:1700000000",
            last_seen_at=now - timedelta(seconds=last_seen_age_seconds),
            started_at=now - timedelta(seconds=last_seen_age_seconds + 5),
        )
        db.add(row)
        await db.flush([row])
        spine_id = row.id
        await db.commit()
    return spine_id, run_id


async def test_reap_marks_stale_rows_lost(async_client):
    ws_id, identity_id = await _make_workspace(async_client)

    stale_ids = [
        (await _seed_run(ws_id=ws_id, identity_id=identity_id, last_seen_age_seconds=2000))[0],
        (await _seed_run(ws_id=ws_id, identity_id=identity_id, last_seen_age_seconds=3600))[0],
    ]
    fresh_id, _ = await _seed_run(ws_id=ws_id, identity_id=identity_id, last_seen_age_seconds=30)

    summary = await reap_stale_inflight_runs({})
    assert summary["status"] == "ok"
    assert summary["stale_seen"] >= 2
    assert summary["reaped"] >= 2

    factory = get_session_factory()
    async with factory() as db:
        for spine_id in stale_ids:
            row = await db.get(InflightRun, spine_id)
            assert row is not None
            assert row.state == InflightRunState.LOST
            assert row.error_kind == "heartbeat_timeout"
            assert row.finished_at is not None

        fresh = await db.get(InflightRun, fresh_id)
        assert fresh is not None
        assert fresh.state == InflightRunState.RUNNING

        audit = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "inflight_run.timed_out_to_lost")
                    .where(AuditEvent.workspace_id == uuid.UUID(ws_id))
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) >= 2

        notif = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "notification.emitted")
                    .where(AuditEvent.workspace_id == uuid.UUID(ws_id))
                )
            )
            .scalars()
            .all()
        )
        assert len(notif) >= 2


async def test_reap_spares_same_host_live_pid(async_client):
    """A stale row whose PID is still alive on this host stays RUNNING.

    The cron will revisit it in 5 minutes; the cheap PID probe avoids
    flipping a slow-but-healthy worker to LOST.
    """
    ws_id, identity_id = await _make_workspace(async_client)

    import socket

    live_token = f"{socket.gethostname()}:{os.getpid()}:1700000000"
    spine_id, _ = await _seed_run(
        ws_id=ws_id,
        identity_id=identity_id,
        last_seen_age_seconds=2000,
        pid_token=live_token,
    )

    summary = await reap_stale_inflight_runs({})
    assert summary["spared_alive"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(InflightRun, spine_id)
        assert row is not None
        assert row.state == InflightRunState.RUNNING
