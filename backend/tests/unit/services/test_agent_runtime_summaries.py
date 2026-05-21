"""Unit tests for :func:`app.services.agent_runtime.build_workspace_summaries`.

Covers:

* Counts are correctly aggregated across multiple workspaces (the
  workspace switcher fans out across every membership in one query).
* Membership filtering: only workspaces the identity has ACTIVE
  membership in are returned.
* The stuck heuristic (``_derive_stuck_reason``) is honoured when
  rolling up the per-workspace counters.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.inflight_run import InflightRunState
from app.repositories.inflight_run import InflightRunRepository
from app.services import agent_runtime as runtime_svc
from app.services import inflight_run as inflight_svc
from app.services import workspace as ws_svc

pytestmark = pytest.mark.asyncio


async def _create_session(db_session, workspace, identity):
    from app.services import session as session_svc

    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        title=f"summary-{uuid.uuid4().hex[:6]}",
    )


async def _register(db_session, workspace, identity, *, agent=None):
    sess = await _create_session(db_session, workspace, identity)
    return await inflight_svc.register_run(
        db_session,
        run_id=uuid.uuid4(),
        session_id=sess.id,
        workspace_id=workspace.id,
        backend_kind="native",
        agent_id=agent.id if agent is not None else None,
        identity_id=identity.id,
        request_snapshot={"trigger": "summary-test"},
    )


async def test_returns_empty_for_identity_with_no_memberships(
    db_session, identity
):
    summaries = await runtime_svc.build_workspace_summaries(
        db_session, identity_id=identity.id
    )
    assert summaries == []


async def test_aggregates_counts_across_workspaces(
    db_session, identity
):
    ws_a = await ws_svc.create_workspace(
        db_session,
        name=f"A-{uuid.uuid4().hex[:6]}",
        slug=f"a-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    ws_b = await ws_svc.create_workspace(
        db_session,
        name=f"B-{uuid.uuid4().hex[:6]}",
        slug=f"b-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    await _register(db_session, ws_a, identity)
    await _register(db_session, ws_a, identity)
    await _register(db_session, ws_b, identity)

    summaries = await runtime_svc.build_workspace_summaries(
        db_session, identity_id=identity.id
    )
    by_ws = {s.workspace_id: s for s in summaries}
    assert by_ws[ws_a.id].running == 2
    assert by_ws[ws_b.id].running == 1
    # No active subscribers ⇒ orphan = running for both
    assert by_ws[ws_a.id].orphan == 2
    assert by_ws[ws_b.id].orphan == 1
    assert by_ws[ws_a.id].stuck == 0
    assert by_ws[ws_b.id].stuck == 0


async def test_excludes_workspaces_without_membership(
    db_session, identity
):
    """A workspace the identity doesn't belong to must not appear."""
    from app.services import auth as auth_svc

    other = await auth_svc.register(
        db_session,
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        name="Other Owner",
        password="summary-test-password",
        create_personal_workspace=False,
    )
    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other-{uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=other.identity.id,
    )
    own_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Own-{uuid.uuid4().hex[:6]}",
        slug=f"own-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    await _register(db_session, other_ws, other.identity)
    await _register(db_session, own_ws, identity)

    summaries = await runtime_svc.build_workspace_summaries(
        db_session, identity_id=identity.id
    )
    ws_ids = {s.workspace_id for s in summaries}
    assert own_ws.id in ws_ids
    assert other_ws.id not in ws_ids


async def test_classifies_stuck_via_hard_cap(
    db_session, identity
):
    """A row whose ``started_at`` is older than ``HARD_CAP_MS`` is stuck."""
    workspace = await ws_svc.create_workspace(
        db_session,
        name=f"Stuck-{uuid.uuid4().hex[:6]}",
        slug=f"stuck-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    row = await _register(db_session, workspace, identity)
    repo = InflightRunRepository(db_session)
    raw = await repo.get(row.id)
    assert raw is not None
    raw.started_at = utcnow_naive() - timedelta(
        milliseconds=runtime_svc.HARD_CAP_MS + 60_000
    )
    await db_session.flush()

    summaries = await runtime_svc.build_workspace_summaries(
        db_session, identity_id=identity.id
    )
    by_ws = {s.workspace_id: s for s in summaries}
    assert by_ws[workspace.id].stuck >= 1


async def test_ignores_terminal_rows(db_session, identity):
    workspace = await ws_svc.create_workspace(
        db_session,
        name=f"Done-{uuid.uuid4().hex[:6]}",
        slug=f"done-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    row = await _register(db_session, workspace, identity)
    await inflight_svc.transition(
        db_session,
        run_id=row.run_id,
        target_state=InflightRunState.COMPLETED,
    )

    summaries = await runtime_svc.build_workspace_summaries(
        db_session, identity_id=identity.id
    )
    by_ws = {s.workspace_id: s for s in summaries}
    assert by_ws[workspace.id].running == 0
