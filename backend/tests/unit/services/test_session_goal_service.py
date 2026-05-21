"""Service-layer unit tests for session_goal (M0.1).

Covers the happy path (lock → score → unlock) plus the cross-tenant
RBAC failure case: a goal id from workspace A must not resolve when
queried with workspace B's id.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.services import session as session_svc
from app.services import session_goal as goal_svc

pytestmark = pytest.mark.asyncio


async def _seed_session(db_session, workspace, identity):
    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )


async def test_lock_goal_happy_path(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    row = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="Ship the M0.1 milestone by Friday",
        success_criteria=["Backend table merged", "Banner visible"],
        alignment_threshold=0.55,
    )
    assert row.workspace_id == workspace.id
    assert row.session_id == sess.id
    assert row.alignment_threshold == 0.55
    assert row.success_criteria == ["Backend table merged", "Banner visible"]
    assert row.unlocked_at is None

    active = await goal_svc.get_active_goal(
        db_session, session_id=sess.id, workspace_id=workspace.id
    )
    assert active is not None
    assert active.id == row.id


async def test_lock_goal_rejects_duplicate(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="first goal",
    )
    with pytest.raises(Conflict):
        await goal_svc.lock_goal(
            db_session,
            session_id=sess.id,
            workspace_id=workspace.id,
            identity_id=identity.id,
            goal_text="second goal — must be rejected",
        )


async def test_lock_goal_rejects_blank_text(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    with pytest.raises(ValidationFailed):
        await goal_svc.lock_goal(
            db_session,
            session_id=sess.id,
            workspace_id=workspace.id,
            identity_id=identity.id,
            goal_text="   ",
        )


async def test_unlock_then_relock_works(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    g1 = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="goal one",
    )
    await goal_svc.unlock_goal(
        db_session,
        goal_id=g1.id,
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
    )
    g2 = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="goal two",
    )
    assert g2.id != g1.id


async def test_get_goal_rejects_cross_workspace(db_session, workspace, identity):
    """RBAC: a goal from workspace A must 404 under workspace B's scope."""
    sess = await _seed_session(db_session, workspace, identity)
    g = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="confidential",
    )
    other_workspace_id = uuid.uuid4()
    with pytest.raises(NotFound):
        await goal_svc.get_goal_or_404(
            db_session, goal_id=g.id, workspace_id=other_workspace_id
        )


async def test_update_goal_threshold_audit(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    g = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="initial goal",
    )
    updated = await goal_svc.update_goal(
        db_session,
        goal_id=g.id,
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
        alignment_threshold=0.8,
    )
    assert updated.alignment_threshold == 0.8


async def test_record_score_flags_below_threshold(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    g = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="be helpful",
        alignment_threshold=0.6,
    )
    msg = await session_svc.append_message(
        db_session,
        session_obj=sess,
        role=session_svc.MessageRole.ASSISTANT,
        content_json={"text": "off-topic answer"},
    )
    score = await goal_svc.record_score(
        db_session,
        session_goal_id=g.id,
        message_id=msg.id,
        workspace_id=workspace.id,
        score=0.3,
        rationale="diverged",
        judged_by_model="test:fake",
    )
    assert score.flagged is True

    score2 = await goal_svc.record_score(
        db_session,
        session_goal_id=g.id,
        message_id=msg.id,
        workspace_id=workspace.id,
        score=0.95,
        rationale="great",
        judged_by_model="test:fake",
    )
    assert score2.flagged is False


async def test_record_score_rejects_cross_workspace(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    g = await goal_svc.lock_goal(
        db_session,
        session_id=sess.id,
        workspace_id=workspace.id,
        identity_id=identity.id,
        goal_text="goal",
    )
    msg = await session_svc.append_message(
        db_session,
        session_obj=sess,
        role=session_svc.MessageRole.ASSISTANT,
        content_json={"text": "answer"},
    )
    other_workspace_id = uuid.uuid4()
    with pytest.raises(NotFound):
        await goal_svc.record_score(
            db_session,
            session_goal_id=g.id,
            message_id=msg.id,
            workspace_id=other_workspace_id,
            score=0.5,
            rationale="x",
            judged_by_model="test:fake",
        )
