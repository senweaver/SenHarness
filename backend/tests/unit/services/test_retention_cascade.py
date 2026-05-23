"""GDPR cascade soft-delete unit tests (M0.11).

Covers ``cascade_for_identity`` / ``cascade_for_workspace`` against the
real DB via the ``db_session`` fixture: seeds rows in
``session_goals`` + ``session_artifacts`` + ``email_verification_tokens``
+ ``goal_alignment_scores`` and asserts the cascade matches the
:data:`CASCADE_TARGETS` policy.

The optional roadmap targets (``judge_verdicts``, ``pending_memories``,
``workspace_creation_logs``) are intentionally not seeded — instead we
prove the cascade silently skips a missing table via the public dict
return.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.session_artifact import SessionArtifact
from app.db.models.session_goal import GoalAlignmentScore, SessionGoal
from app.services import retention as retention_svc

pytestmark = pytest.mark.asyncio


async def _make_session(db_session, workspace, identity) -> uuid.UUID:
    """Insert a minimal session row via raw SQL to avoid pulling the
    full ``Session`` model graph into every test.
    """
    sid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": sid, "ws": workspace.id, "uid": identity.id},
    )
    return sid


async def _make_message(db_session, workspace, session_id) -> uuid.UUID:
    mid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO messages (id, workspace_id, session_id, role, "
            "content_json, attachments_json, token_usage_json, metadata_json, "
            "created_at) "
            "VALUES (:id, :ws, :sid, 'assistant', '{}'::jsonb, "
            "'[]'::jsonb, '{}'::jsonb, '{}'::jsonb, now())"
        ),
        {"id": mid, "ws": workspace.id, "sid": session_id},
    )
    return mid


async def _seed_goal(db_session, workspace, identity) -> SessionGoal:
    sid = await _make_session(db_session, workspace, identity)
    goal = SessionGoal(
        workspace_id=workspace.id,
        session_id=sid,
        goal_text="ship M0.11",
        success_criteria=[],
        locked_by=identity.id,
        alignment_threshold=0.6,
        metadata_json={},
    )
    db_session.add(goal)
    await db_session.flush()
    return goal


async def _seed_artifact(db_session, workspace, identity, *, agent_id=None) -> SessionArtifact:
    sid = await _make_session(db_session, workspace, identity)
    artifact = SessionArtifact(
        workspace_id=workspace.id,
        run_id=uuid.uuid4(),
        session_id=sid,
        agent_id=agent_id,
        identity_id=identity.id,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=[],
        iteration_count=0,
        final_outcome="success",
        finished_at=utcnow_naive(),
    )
    db_session.add(artifact)
    await db_session.flush()
    return artifact


async def _seed_alignment_score(
    db_session, workspace, identity, goal: SessionGoal
) -> GoalAlignmentScore:
    mid = await _make_message(db_session, workspace, goal.session_id)
    score = GoalAlignmentScore(
        workspace_id=workspace.id,
        session_goal_id=goal.id,
        message_id=mid,
        score=0.8,
        rationale="seed",
        flagged=False,
    )
    db_session.add(score)
    await db_session.flush()
    return score


async def _seed_email_token(db_session, identity) -> EmailVerificationToken:
    tok = EmailVerificationToken(
        identity_id=identity.id,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=utcnow_naive() + timedelta(hours=1),
    )
    db_session.add(tok)
    await db_session.flush()
    return tok


async def test_cascade_for_identity_soft_deletes_owned_rows(db_session, workspace, identity):
    goal = await _seed_goal(db_session, workspace, identity)
    artifact = await _seed_artifact(db_session, workspace, identity)
    score = await _seed_alignment_score(db_session, workspace, identity, goal)
    token = await _seed_email_token(db_session, identity)

    affected = await retention_svc.cascade_for_identity(db_session, identity_id=identity.id)

    assert "session_goals" in affected
    assert "session_artifacts" in affected
    assert "goal_alignment_scores" in affected
    assert "email_verification_tokens" in affected
    assert affected["session_goals"] >= 1
    assert affected["session_artifacts"] >= 1
    assert affected["goal_alignment_scores"] >= 1
    assert affected["email_verification_tokens"] >= 1

    fresh_goal = (
        await db_session.execute(select(SessionGoal).where(SessionGoal.id == goal.id))
    ).scalar_one()
    assert fresh_goal.deleted_at is not None

    fresh_artifact = (
        await db_session.execute(select(SessionArtifact).where(SessionArtifact.id == artifact.id))
    ).scalar_one()
    assert fresh_artifact.deleted_at is not None

    # goal_alignment_scores has no soft-delete column → should be gone.
    remaining_score = (
        await db_session.execute(
            select(GoalAlignmentScore).where(GoalAlignmentScore.id == score.id)
        )
    ).scalar_one_or_none()
    assert remaining_score is None

    # email_verification_tokens is hard-delete on cascade.
    remaining_token = (
        await db_session.execute(
            select(EmailVerificationToken).where(EmailVerificationToken.id == token.id)
        )
    ).scalar_one_or_none()
    assert remaining_token is None


async def test_cascade_for_identity_is_idempotent(db_session, workspace, identity):
    goal = await _seed_goal(db_session, workspace, identity)
    await _seed_artifact(db_session, workspace, identity)

    first = await retention_svc.cascade_for_identity(db_session, identity_id=identity.id)
    assert first["session_goals"] >= 1

    second = await retention_svc.cascade_for_identity(db_session, identity_id=identity.id)
    # After the first pass every soft-delete row already carries
    # ``deleted_at`` so the predicate excludes them; the second pass
    # must report zero affected rows for those tables.
    assert second["session_goals"] == 0
    assert second["session_artifacts"] == 0
    # Hard-delete tables (email_verification_tokens / goal_alignment_scores)
    # also report 0 because the rows were already removed.
    assert second.get("email_verification_tokens", 0) == 0
    _ = goal


async def test_cascade_for_workspace_soft_deletes_workspace_scoped(db_session, workspace, identity):
    goal = await _seed_goal(db_session, workspace, identity)
    artifact = await _seed_artifact(db_session, workspace, identity)
    await _seed_alignment_score(db_session, workspace, identity, goal)
    token = await _seed_email_token(db_session, identity)

    affected = await retention_svc.cascade_for_workspace(db_session, workspace_id=workspace.id)

    assert affected["session_goals"] >= 1
    assert affected["session_artifacts"] >= 1
    assert affected["goal_alignment_scores"] >= 1
    # Identity-only tables must NOT appear in workspace cascade output.
    assert "email_verification_tokens" not in affected
    assert "workspace_creation_logs" not in affected

    fresh_goal = (
        await db_session.execute(select(SessionGoal).where(SessionGoal.id == goal.id))
    ).scalar_one()
    assert fresh_goal.deleted_at is not None

    fresh_artifact = (
        await db_session.execute(select(SessionArtifact).where(SessionArtifact.id == artifact.id))
    ).scalar_one()
    assert fresh_artifact.deleted_at is not None

    # Identity-scoped token survives a workspace-only cascade.
    surviving = (
        await db_session.execute(
            select(EmailVerificationToken).where(EmailVerificationToken.id == token.id)
        )
    ).scalar_one_or_none()
    assert surviving is not None


async def test_cascade_skips_missing_tables(db_session, workspace, identity):
    """Cascade silently omits tables not present in the schema rather than
    raising. Asserts on a table whose migration hasn't shipped — once it
    does, replace it with another not-yet-migrated table.
    """
    affected = await retention_svc.cascade_for_identity(db_session, identity_id=identity.id)
    assert "workspace_creation_logs" not in affected


async def test_scope_id_hash_is_stable_and_short():
    uid = uuid.uuid4()
    h1 = retention_svc.scope_id_hash(uid)
    h2 = retention_svc.scope_id_hash(str(uid))
    assert h1 == h2
    assert len(h1) == 16
    assert h1 != retention_svc.scope_id_hash(uuid.uuid4())
