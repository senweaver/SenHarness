"""Unit tests for ``app.services.subagent_run`` lifecycle helpers (M2.5.1).

Covers the state machine edges, heartbeat update, retry budget
consumption + exhaustion, and the stale-row scan that the
``reap_zombies`` cron consumes. Uses the shared ``db_session`` fixture
so the suite skips cleanly when Postgres isn't available.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.repositories.subagent_run import SubAgentRunRepository
from app.services import subagent_run as svc

pytestmark = pytest.mark.asyncio


async def _register(db_session, workspace, **kwargs) -> SubAgentRun:
    parent_run_id = kwargs.pop("parent_run_id", uuid.uuid4())
    child_run_id = kwargs.pop("child_run_id", uuid.uuid4())
    return await svc.register_run(
        db_session,
        workspace_id=workspace.id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        **kwargs,
    )


async def test_register_run_is_idempotent_on_child_run_id(db_session, workspace):
    parent = uuid.uuid4()
    child = uuid.uuid4()
    first = await _register(
        db_session, workspace, parent_run_id=parent, child_run_id=child
    )
    second = await _register(
        db_session, workspace, parent_run_id=parent, child_run_id=child
    )
    assert first.id == second.id
    assert second.state == SubAgentRunState.RUNNING


async def test_transition_state_writes_audit_and_terminal_is_sticky(
    db_session, workspace
):
    row = await _register(db_session, workspace)
    updated = await svc.transition_state(
        db_session,
        child_run_id=row.child_run_id,
        target_state=SubAgentRunState.COMPLETED,
        reason="happy",
        final_output="hi",
    )
    assert updated.state == SubAgentRunState.COMPLETED
    assert updated.final_output == "hi"

    # Already terminal — second transition is a no-op (no FAILED).
    again = await svc.transition_state(
        db_session,
        child_run_id=row.child_run_id,
        target_state=SubAgentRunState.FAILED,
        reason="late",
        error_kind="oops",
    )
    assert again.state == SubAgentRunState.COMPLETED


async def test_update_heartbeat_bumps_timestamp(db_session, workspace):
    row = await _register(db_session, workspace)
    original = row.last_heartbeat_at
    later = original + timedelta(seconds=45)
    ok = await svc.update_heartbeat(
        db_session, child_run_id=row.child_run_id, now=later
    )
    assert ok is True
    refreshed = await SubAgentRunRepository(db_session).get(row.id)
    assert refreshed is not None
    assert refreshed.last_heartbeat_at >= later


async def test_update_heartbeat_skips_terminal_rows(db_session, workspace):
    row = await _register(db_session, workspace)
    await svc.transition_state(
        db_session,
        child_run_id=row.child_run_id,
        target_state=SubAgentRunState.COMPLETED,
    )
    ok = await svc.update_heartbeat(db_session, child_run_id=row.child_run_id)
    assert ok is False


async def test_consume_retry_budget_returns_remaining(db_session, workspace):
    row = await _register(db_session, workspace, retry_budget=3)
    remaining = await svc.consume_retry_budget(
        db_session, child_run_id=row.child_run_id
    )
    assert remaining == 1
    remaining = await svc.consume_retry_budget(
        db_session, child_run_id=row.child_run_id
    )
    assert remaining == 0


async def test_consume_retry_budget_raises_when_exhausted(db_session, workspace):
    row = await _register(db_session, workspace, retry_budget=1)
    await svc.consume_retry_budget(db_session, child_run_id=row.child_run_id)
    with pytest.raises(svc.RetryBudgetExhausted):
        await svc.consume_retry_budget(
            db_session, child_run_id=row.child_run_id
        )


async def test_list_stale_picks_old_running_rows(db_session, workspace):
    fresh = await _register(db_session, workspace)
    stale = await _register(db_session, workspace)
    completed = await _register(db_session, workspace)
    # Force the stale row to look ancient + the completed one terminal.
    repo = SubAgentRunRepository(db_session)
    stale_row = await repo.get(stale.id)
    assert stale_row is not None
    stale_row.last_heartbeat_at = utcnow_naive() - timedelta(minutes=10)
    await db_session.flush([stale_row])
    await svc.transition_state(
        db_session,
        child_run_id=completed.child_run_id,
        target_state=SubAgentRunState.COMPLETED,
    )

    stale_rows = await svc.list_stale(
        db_session, heartbeat_dead_seconds=300, limit=50
    )
    ids = {r.child_run_id for r in stale_rows}
    assert stale.child_run_id in ids
    assert fresh.child_run_id not in ids
    assert completed.child_run_id not in ids


async def test_reap_zombie_marks_state_and_writes_audit(db_session, workspace):
    row = await _register(db_session, workspace)
    repo = SubAgentRunRepository(db_session)
    live = await repo.get(row.id)
    assert live is not None
    live.last_heartbeat_at = utcnow_naive() - timedelta(minutes=10)
    await db_session.flush([live])

    reaped = await svc.reap_zombie(
        db_session, child_run_id=row.child_run_id, reason="test"
    )
    assert reaped.state == SubAgentRunState.ZOMBIE
    assert reaped.error_kind == "heartbeat_lost"
