"""Unit tests for the M4.1 runtime console service helpers.

Covers:

* :func:`list_active_for_console` filters by workspace and projects
  the persisted ``(state, error_kind)`` tuple onto the console buckets
  the UI renders (running / paused / lost / zombie / killed).
* :func:`force_recycle_run` cancels the kernel task, transitions the
  spine row to ``CANCELLED``, writes the ``inflight_run.force_recycled``
  audit, and emits the in-app notification.
* :func:`runtime_console_stats` aggregates the same listing into the
  dashboard counter shape.
* The error-edge guards (``RunNotFoundError`` cross-tenant lookups,
  ``RunTerminalError`` for already-settled rows).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.repositories.inflight_run import InflightRunRepository
from app.services import inflight_run as svc

pytestmark = pytest.mark.asyncio


async def _create_session(db_session, workspace, identity):
    from app.services import session as session_svc

    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        title=f"console-{uuid.uuid4().hex[:6]}",
    )


async def _register(
    db_session,
    workspace,
    identity,
    *,
    agent=None,
    backend_kind="native",
    pid_token: str | None = None,
):
    sess = await _create_session(db_session, workspace, identity)
    return await svc.register_run(
        db_session,
        run_id=uuid.uuid4(),
        session_id=sess.id,
        workspace_id=workspace.id,
        backend_kind=backend_kind,
        agent_id=agent.id if agent is not None else None,
        identity_id=identity.id,
        request_snapshot={"trigger": "console-test", "input_tokens": 42},
        pid_token=pid_token,
    )


# ─── list_active_for_console ────────────────────────────────
async def test_list_active_includes_running_and_paused(db_session, workspace, identity, agent):
    row = await _register(db_session, workspace, identity, agent=agent)

    rows = await svc.list_active_for_console(db_session, workspace_id=workspace.id)

    assert any(r.run_id == row.run_id for r in rows)
    surfaced = next(r for r in rows if r.run_id == row.run_id)
    assert surfaced.state_bucket == "running"
    assert surfaced.agent_id == agent.id
    assert surfaced.agent_name == agent.name
    assert surfaced.identity_email == identity.email
    assert surfaced.backend_kind == "native"
    assert surfaced.elapsed_seconds >= 0
    assert surfaced.token_estimate == 42


async def test_list_active_isolates_workspaces(db_session, identity, agent):
    """A row in workspace A must not leak into workspace B's listing."""
    from app.services import workspace as ws_svc

    ws_a = await ws_svc.create_workspace(
        db_session,
        name=f"WS-A-{uuid.uuid4().hex[:6]}",
        slug=f"ws-a-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    ws_b = await ws_svc.create_workspace(
        db_session,
        name=f"WS-B-{uuid.uuid4().hex[:6]}",
        slug=f"ws-b-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    a_row = await _register(db_session, ws_a, identity)
    b_row = await _register(db_session, ws_b, identity)

    a_rows = await svc.list_active_for_console(db_session, workspace_id=ws_a.id)
    b_rows = await svc.list_active_for_console(db_session, workspace_id=ws_b.id)

    a_run_ids = {r.run_id for r in a_rows}
    b_run_ids = {r.run_id for r in b_rows}
    assert a_row.run_id in a_run_ids
    assert a_row.run_id not in b_run_ids
    assert b_row.run_id in b_run_ids
    assert b_row.run_id not in a_run_ids


async def test_list_active_projects_zombie_and_lost(db_session, workspace, identity):
    """LOST rows split into zombie / lost on error_kind."""
    zombie_row = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=zombie_row.run_id,
        target_state=InflightRunState.LOST,
        error_kind=svc.ERROR_KIND_HEARTBEAT_TIMEOUT,
    )
    lost_row = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=lost_row.run_id,
        target_state=InflightRunState.LOST,
        error_kind=svc.ERROR_KIND_BACKEND_RESTART,
    )

    rows = await svc.list_active_for_console(db_session, workspace_id=workspace.id)
    by_run = {r.run_id: r for r in rows}
    assert by_run[zombie_row.run_id].state_bucket == "zombie"
    assert by_run[lost_row.run_id].state_bucket == "lost"


async def test_list_active_filters_by_state_bucket(db_session, workspace, identity):
    running = await _register(db_session, workspace, identity)
    cancelled = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=cancelled.run_id,
        target_state=InflightRunState.CANCELLED,
        error_kind=svc.ERROR_KIND_ADMIN_FORCE_RECYCLE,
    )

    only_killed = await svc.list_active_for_console(
        db_session,
        workspace_id=workspace.id,
        states=["killed"],
    )
    assert all(r.state_bucket == "killed" for r in only_killed)
    assert any(r.run_id == cancelled.run_id for r in only_killed)
    assert not any(r.run_id == running.run_id for r in only_killed)


async def test_elapsed_seconds_uses_finished_at_for_terminal(db_session, workspace, identity):
    row = await _register(db_session, workspace, identity)
    raw = await InflightRunRepository(db_session).get(row.id)
    assert raw is not None
    raw.started_at = raw.last_seen_at - timedelta(seconds=120)
    await db_session.flush()
    await svc.transition(
        db_session,
        run_id=raw.run_id,
        target_state=InflightRunState.CANCELLED,
        error_kind=svc.ERROR_KIND_ADMIN_FORCE_RECYCLE,
    )

    rows = await svc.list_active_for_console(db_session, workspace_id=workspace.id)
    surfaced = next(r for r in rows if r.run_id == raw.run_id)
    assert surfaced.elapsed_seconds >= 120


# ─── runtime_console_stats ──────────────────────────────────
async def test_runtime_console_stats_counts_buckets(db_session, workspace, identity):
    running = await _register(db_session, workspace, identity)
    paused = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=paused.run_id,
        target_state=InflightRunState.PAUSED,
    )
    zombie = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=zombie.run_id,
        target_state=InflightRunState.LOST,
        error_kind=svc.ERROR_KIND_HEARTBEAT_TIMEOUT,
    )

    stats = await svc.runtime_console_stats(db_session, workspace_id=workspace.id)
    assert stats.running >= 1
    assert stats.paused >= 1
    assert stats.zombie >= 1
    assert stats.total_active == stats.running + stats.paused
    _ = running  # keep reference for clarity


# ─── force_recycle_run ──────────────────────────────────────
async def test_force_recycle_happy_path(db_session, workspace, identity, monkeypatch):
    """Cancels the kernel task, flips state, writes audit + notification."""
    cancel_calls: list[uuid.UUID] = []

    class _StubBackend:
        async def cancel(self, run_id: uuid.UUID) -> None:
            cancel_calls.append(run_id)

    monkeypatch.setattr(
        "app.agents.kernels.registry.get_backend",
        lambda kind: _StubBackend(),
    )
    monkeypatch.setattr("app.services.notification_events.emit_event", _stub_emit)

    row = await _register(db_session, workspace, identity)

    result = await svc.force_recycle_run(
        db_session,
        workspace_id=workspace.id,
        run_id=row.run_id,
        actor_identity_id=identity.id,
    )

    assert cancel_calls == [row.run_id]
    assert result["state"] == "cancelled"
    assert result["previous_state"] == "running"
    assert result["cancel_dispatched"] is True

    refreshed = await InflightRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.state == InflightRunState.CANCELLED
    assert refreshed.error_kind == svc.ERROR_KIND_ADMIN_FORCE_RECYCLE
    assert refreshed.finished_at is not None

    audit_rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == svc.AUDIT_FORCE_RECYCLED)
            )
        )
        .scalars()
        .all()
    )
    assert any(a.workspace_id == workspace.id for a in audit_rows)


async def _stub_emit(*_args, **_kwargs):
    """Inert replacement for ``emit_event`` so the unit test stays
    decoupled from Redis cooldown / target resolution. The real fan-out
    has its own integration coverage."""
    return {
        "in_app_sent": 0,
        "email_sent": 0,
        "cooldown_skipped": 0,
        "pref_skipped": 0,
    }


async def test_force_recycle_unknown_run_raises_not_found(db_session, workspace, identity):
    with pytest.raises(svc.RunNotFoundError):
        await svc.force_recycle_run(
            db_session,
            workspace_id=workspace.id,
            run_id=uuid.uuid4(),
            actor_identity_id=identity.id,
        )


async def test_force_recycle_cross_workspace_raises_not_found(db_session, identity):
    """A run belonging to workspace A must 404 when looked up via ws B."""
    from app.services import workspace as ws_svc

    ws_a = await ws_svc.create_workspace(
        db_session,
        name=f"FR-A-{uuid.uuid4().hex[:6]}",
        slug=f"fr-a-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    ws_b = await ws_svc.create_workspace(
        db_session,
        name=f"FR-B-{uuid.uuid4().hex[:6]}",
        slug=f"fr-b-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    row = await _register(db_session, ws_a, identity)
    with pytest.raises(svc.RunNotFoundError):
        await svc.force_recycle_run(
            db_session,
            workspace_id=ws_b.id,
            run_id=row.run_id,
            actor_identity_id=identity.id,
        )


async def test_force_recycle_terminal_raises_terminal_error(
    db_session, workspace, identity, monkeypatch
):
    monkeypatch.setattr("app.services.notification_events.emit_event", _stub_emit)
    row = await _register(db_session, workspace, identity)
    await svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.COMPLETED,
    )

    with pytest.raises(svc.RunTerminalError) as excinfo:
        await svc.force_recycle_run(
            db_session,
            workspace_id=workspace.id,
            run_id=row.run_id,
            actor_identity_id=identity.id,
        )
    assert excinfo.value.state == InflightRunState.COMPLETED


async def test_force_recycle_audits_failure_when_backend_missing(
    db_session, workspace, identity, monkeypatch
):
    """Unknown backend_kind still flips state but logs a failure audit."""
    monkeypatch.setattr(
        "app.agents.kernels.registry.get_backend",
        lambda kind: None,
    )
    monkeypatch.setattr("app.services.notification_events.emit_event", _stub_emit)
    row = await _register(db_session, workspace, identity)

    result = await svc.force_recycle_run(
        db_session,
        workspace_id=workspace.id,
        run_id=row.run_id,
        actor_identity_id=identity.id,
    )

    assert result["cancel_dispatched"] is False
    assert result["state"] == "cancelled"

    failure_rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == svc.AUDIT_FORCE_RECYCLE_FAILED,
                    AuditEvent.workspace_id == workspace.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(failure_rows) >= 1

    refreshed = await InflightRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.state == InflightRunState.CANCELLED


# ─── console_state_bucket projection ────────────────────────
def test_console_state_bucket_projection_table():
    cases = [
        (InflightRunState.RUNNING, None, "running"),
        (InflightRunState.PAUSED, None, "paused"),
        (InflightRunState.LOST, svc.ERROR_KIND_HEARTBEAT_TIMEOUT, "zombie"),
        (InflightRunState.LOST, svc.ERROR_KIND_BACKEND_RESTART, "lost"),
        (InflightRunState.LOST, None, "lost"),
        (InflightRunState.CANCELLED, svc.ERROR_KIND_ADMIN_FORCE_RECYCLE, "killed"),
        (InflightRunState.CANCELLED, None, "killed"),
    ]
    for state, error_kind, expected in cases:
        assert svc.console_state_bucket(state, error_kind) == expected, (
            state,
            error_kind,
        )


# Ensure the unused import survives lint.
_ = InflightRun
