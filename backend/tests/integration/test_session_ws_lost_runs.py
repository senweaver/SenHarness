"""Integration: M2.5.2 WS reconnect surfaces LOST inflight_runs.

The chat WebSocket emits a ``system`` / ``lost_runs`` frame the first
time a client reconnects after one of its turns was reaped. This test
drives the same code path the handshake hits (``recover_inflight_runs``
+ ``list_lost_for_session``) without needing an actual WebSocket
client, because the FastAPI test surface only exposes ``httpx`` which
doesn't speak WS.

Asserts:

1. A backend-restart recovery flips a stale RUNNING row to LOST,
   writes the ``inflight_run.recovered_lost`` audit, and emits the
   ``inflight_run.lost_detected`` notification to the run's owner.
2. ``list_lost_for_session`` returns the same row so the WS handshake
   can serialise the ``system`` frame.
3. The ``system`` payload shape matches what the WS endpoint sends:
   ``{type:'system', data:{kind:'lost_runs', count, run_ids, ...}}``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.models.notification import Notification
from app.services import inflight_run as svc

pytestmark = pytest.mark.asyncio


async def _seed_running_row(
    db_session,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID,
    pid_token: str,
) -> InflightRun:
    row = InflightRun(
        workspace_id=workspace_id,
        run_id=uuid.uuid4(),
        session_id=session_id,
        identity_id=identity_id,
        backend_kind="native",
        request_snapshot={"trigger": "test"},
        state=InflightRunState.RUNNING,
        pid_token=pid_token,
        last_seen_at=utcnow_naive(),
        started_at=utcnow_naive(),
    )
    db_session.add(row)
    await db_session.flush([row])
    return row


def _build_system_frame(
    *, session_id: uuid.UUID, lost_rows: list[InflightRun]
) -> dict:
    """Mirror of the ``session_ws`` handshake payload."""
    return {
        "type": "system",
        "data": {
            "kind": "lost_runs",
            "count": len(lost_rows),
            "run_ids": [str(r.run_id) for r in lost_rows],
            "session_id": str(session_id),
            "message": (
                "Previous run(s) were interrupted by a server restart "
                "or stalled connection. Reply with /retry to resume "
                "or continue with a new request."
            ),
        },
    }


async def test_recover_marks_stale_run_lost_and_emits_notification(
    db_session, workspace, identity
):
    """Backend-restart simulation: stale row → LOST + audit + notification."""
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )

    previous_token = "previous-host:99999:1700000000"
    row = await _seed_running_row(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        pid_token=previous_token,
    )

    result = await svc.recover_inflight_runs(
        db_session,
        current_token=svc.current_pid_token(),
    )
    assert result["recovered_count"] >= 1

    refreshed = await db_session.get(InflightRun, row.id)
    assert refreshed is not None
    assert refreshed.state == InflightRunState.LOST
    assert refreshed.error_kind == svc.ERROR_KIND_BACKEND_RESTART

    audit_actions = (
        await db_session.execute(
            select(AuditEvent.action).where(
                AuditEvent.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert "inflight_run.recovered_lost" in audit_actions

    notifications = (
        await db_session.execute(
            select(Notification).where(
                Notification.recipient_identity_id == identity.id
            )
        )
    ).scalars().all()
    assert any(
        n.kind == "inflight_run.lost_detected" for n in notifications
    )


async def test_list_lost_for_session_drives_ws_handshake_payload(
    db_session, workspace, identity
):
    """``list_lost_for_session`` returns the rows the WS frame must surface."""
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )

    lost_row = await _seed_running_row(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        pid_token="previous:1:1",
    )
    await svc.transition(
        db_session,
        run_id=lost_row.run_id,
        target_state=InflightRunState.LOST,
        error_kind=svc.ERROR_KIND_BACKEND_RESTART,
    )
    # A still-RUNNING row in the same session must NOT show up.
    fresh = await _seed_running_row(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        pid_token=svc.current_pid_token(),
    )

    rows = await svc.list_lost_for_session(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
    )
    assert len(rows) == 1
    assert rows[0].run_id == lost_row.run_id

    frame = _build_system_frame(session_id=sess.id, lost_rows=list(rows))
    assert frame["type"] == "system"
    assert frame["data"]["kind"] == "lost_runs"
    assert frame["data"]["count"] == 1
    assert frame["data"]["run_ids"] == [str(lost_row.run_id)]
    assert frame["data"]["session_id"] == str(sess.id)
    # Sanity: the still-RUNNING row's run_id is absent.
    assert str(fresh.run_id) not in frame["data"]["run_ids"]


async def test_completed_run_does_not_show_up_as_lost(
    db_session, workspace, identity
):
    """A row that finishes normally must never appear in the LOST list."""
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )

    row = await _seed_running_row(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        pid_token=svc.current_pid_token(),
    )
    await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.COMPLETED,
        reason="ok",
    )

    rows = await svc.list_lost_for_session(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
    )
    assert rows == []
