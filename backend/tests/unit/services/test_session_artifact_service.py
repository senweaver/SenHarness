"""Service-layer tests for ``capture_artifact`` (M0.2).

Idempotency, fail-open behaviour, and outcome inference are covered.
The fold algorithm has its own dedicated test module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.services import session as session_svc
from app.services import session_artifact as artifact_svc

pytestmark = pytest.mark.asyncio


async def _seed_session(db_session, workspace, identity):
    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )


async def test_capture_persists_row_with_folded_turns(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    run_id = uuid.uuid4()
    events = [
        {"kind": "delta", "data": {"text": "Hello "}},
        {"kind": "tool_call", "data": {"id": "1", "name": "search", "args": {}}},
        {"kind": "tool_result", "data": {"id": "1", "result": "ok"}},
        {"kind": "delta", "data": {"text": "world"}},
        {"kind": "final", "data": {}},
    ]
    row = await artifact_svc.capture_artifact(
        db_session,
        run_id=run_id,
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="hi there",
        events=events,
        final_outcome="success",
        finished_at=datetime.now(UTC).replace(tzinfo=None),
    )
    assert row.run_id == run_id
    assert row.workspace_id == workspace.id
    assert row.session_id == sess.id
    assert row.iteration_count == 2
    assert row.invoked_tools == ["search"]
    assert row.final_outcome == "success"
    assert len(row.user_text_hash) == 64
    # User turn always lands at index 0 with iteration=0.
    assert row.turns_json[0]["role"] == "user"
    assert row.turns_json[0]["iteration"] == 0


async def test_capture_is_idempotent_on_run_id(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    run_id = uuid.uuid4()
    args: dict = {
        "run_id": run_id,
        "workspace_id": workspace.id,
        "session_id": sess.id,
        "agent_id": None,
        "identity_id": identity.id,
        "user_text": "x",
        "events": [{"kind": "delta", "data": {"text": "y"}}],
        "final_outcome": "success",
    }
    first = await artifact_svc.capture_artifact(db_session, **args)
    second = await artifact_svc.capture_artifact(db_session, **args)
    assert first.id == second.id


async def test_capture_from_run_outcome_infers_success(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    events = [
        {"kind": "delta", "data": {"text": "ok"}},
        {"kind": "final", "data": {}},
    ]
    row = await artifact_svc.capture_from_run_outcome(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        run_id=uuid.uuid4(),
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=events,
        raised_exc=None,
    )
    assert row is not None
    assert row.final_outcome == "success"
    assert row.error_kind is None


async def test_capture_from_run_outcome_marks_cancelled(
    db_session, workspace, identity
):
    import asyncio

    sess = await _seed_session(db_session, workspace, identity)
    row = await artifact_svc.capture_from_run_outcome(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        run_id=uuid.uuid4(),
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=[{"kind": "delta", "data": {"text": "partial"}}],
        raised_exc=asyncio.CancelledError(),
    )
    assert row is not None
    assert row.final_outcome == "cancelled"


async def test_capture_failure_returns_none_and_does_not_break(
    db_session, workspace, identity, monkeypatch
):
    """A blow-up inside ``capture_artifact`` must not propagate; the
    wrapper writes an audit row and returns ``None`` instead."""

    sess = await _seed_session(db_session, workspace, identity)

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(artifact_svc, "capture_artifact", _explode)

    row = await artifact_svc.capture_from_run_outcome(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        run_id=uuid.uuid4(),
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=[],
        raised_exc=None,
    )
    assert row is None


async def test_get_artifact_by_id_rejects_cross_workspace(
    db_session, workspace, identity
):
    from app.core.errors import NotFound

    sess = await _seed_session(db_session, workspace, identity)
    row = await artifact_svc.capture_artifact(
        db_session,
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="confidential",
        events=[],
        final_outcome="success",
    )
    other_workspace_id = uuid.uuid4()
    with pytest.raises(NotFound):
        await artifact_svc.get_artifact_by_id(
            db_session,
            workspace_id=other_workspace_id,
            artifact_id=row.id,
        )


async def test_update_judge_score_persists_value(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    row = await artifact_svc.capture_artifact(
        db_session,
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=[],
        final_outcome="success",
    )
    updated = await artifact_svc.update_judge_score(
        db_session,
        workspace_id=workspace.id,
        artifact_id=row.id,
        judge_score=0.42,
    )
    assert updated.judge_score == pytest.approx(0.42)


async def test_update_judge_score_validates_range(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    row = await artifact_svc.capture_artifact(
        db_session,
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=[],
        final_outcome="success",
    )
    with pytest.raises(ValueError):
        await artifact_svc.update_judge_score(
            db_session,
            workspace_id=workspace.id,
            artifact_id=row.id,
            judge_score=2.5,
        )
