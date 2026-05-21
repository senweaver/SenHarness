"""Unit tests for the M2.5.2 ``inflight_run`` service.

Covers register / heartbeat / transition state machine + the startup
``recover_inflight_runs`` sweep with a simulated dead PID. Uses the
shared ``db_session`` fixture so the suite skips cleanly when Postgres
isn't available.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.inflight_run import InflightRun, InflightRunState
from app.repositories.inflight_run import InflightRunRepository
from app.services import inflight_run as svc

pytestmark = pytest.mark.asyncio


async def _register(
    db_session, workspace, *, pid_token: str | None = None, **kwargs
) -> InflightRun:
    sess_id = kwargs.pop("session_id", None)
    if sess_id is None:
        from app.services import session as session_svc

        sess = await session_svc.create_session(
            db_session,
            workspace_id=workspace.id,
            owner_identity_id=None,
        )
        sess_id = sess.id
    return await svc.register_run(
        db_session,
        run_id=kwargs.pop("run_id", uuid.uuid4()),
        session_id=sess_id,
        workspace_id=workspace.id,
        backend_kind=kwargs.pop("backend_kind", "native"),
        request_snapshot=kwargs.pop("request_snapshot", {"trigger": "test"}),
        pid_token=pid_token,
        **kwargs,
    )


async def test_register_run_is_idempotent_on_run_id(db_session, workspace):
    run_id = uuid.uuid4()
    first = await _register(db_session, workspace, run_id=run_id)
    second = await _register(db_session, workspace, run_id=run_id)
    assert first.id == second.id
    assert second.state == InflightRunState.RUNNING


async def test_transition_writes_audit_and_terminal_is_sticky(
    db_session, workspace
):
    row = await _register(db_session, workspace)
    updated = await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.COMPLETED,
        reason="happy path",
    )
    assert updated is not None
    assert updated.state == InflightRunState.COMPLETED
    assert updated.finished_at is not None

    # Already terminal — second transition is a no-op.
    again = await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.FAILED,
        error_kind="late",
    )
    assert again is not None
    assert again.state == InflightRunState.COMPLETED


async def test_update_last_seen_bumps_seq_and_skips_terminal(
    db_session, workspace
):
    row = await _register(db_session, workspace)
    ok = await svc.update_last_seen(
        db_session, run_id=row.run_id, last_event_seq=5
    )
    assert ok is True

    refreshed = await InflightRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.last_event_seq == 5

    await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.COMPLETED,
    )
    skipped = await svc.update_last_seen(
        db_session, run_id=row.run_id, last_event_seq=99
    )
    assert skipped is False


async def test_recover_inflight_runs_marks_other_pid_lost(
    db_session, workspace
):
    # Seed a row that pretends to belong to a previous backend incarnation.
    other_token = "other-host:99999:1700000000"
    row = await _register(db_session, workspace, pid_token=other_token)
    assert row.pid_token == other_token

    current = svc.current_pid_token()
    result = await svc.recover_inflight_runs(
        db_session,
        current_token=current,
        emit_notification=False,
    )

    assert result["recovered_count"] >= 1
    refreshed = await InflightRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.state == InflightRunState.LOST
    assert refreshed.error_kind == svc.ERROR_KIND_BACKEND_RESTART
    assert refreshed.finished_at is not None


async def test_recover_inflight_runs_spares_current_token(
    db_session, workspace
):
    current = svc.current_pid_token()
    row = await _register(db_session, workspace, pid_token=current)

    result = await svc.recover_inflight_runs(
        db_session,
        current_token=current,
        emit_notification=False,
    )
    assert result["alive_count"] >= 1

    refreshed = await InflightRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.state == InflightRunState.RUNNING


async def test_list_lost_for_session_filters_by_state(db_session, workspace):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=None,
    )

    lost_row = await _register(
        db_session,
        workspace,
        session_id=sess.id,
        pid_token="other:1:1",
    )
    await svc.transition(
        db_session,
        run_id=lost_row.run_id,
        target_state=InflightRunState.LOST,
        error_kind=svc.ERROR_KIND_BACKEND_RESTART,
    )
    # A still-running row must NOT show up in the lost list.
    await _register(db_session, workspace, session_id=sess.id)

    rows = await svc.list_lost_for_session(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
    )
    assert len(rows) == 1
    assert rows[0].id == lost_row.id


async def test_transition_failed_audit_when_db_breaks(
    db_session, workspace, monkeypatch
):
    """The transition wrapper writes a stable failure audit instead of raising."""
    row = await _register(db_session, workspace)

    class _BoomError(Exception):
        pass

    async def _broken_get(*_args, **_kwargs):
        raise _BoomError("simulated repo crash")

    monkeypatch.setattr(
        InflightRunRepository, "get_by_run_id", _broken_get
    )

    out = await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.FAILED,
    )
    assert out is None
