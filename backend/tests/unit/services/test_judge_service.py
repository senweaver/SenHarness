"""Service-layer tests for ``persist_verdict`` (M0.3).

Cover the atomic ``judge_verdicts`` upsert + ``session_artifacts.judge_score``
mirror, idempotency on ``artifact_id``, and the score → float mapping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.services import judge as judge_svc
from app.services import session as session_svc
from app.services import session_artifact as artifact_svc

pytestmark = pytest.mark.asyncio


async def _seed_artifact(db_session, workspace, identity):
    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    artifact = await artifact_svc.capture_artifact(
        db_session,
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="hi",
        events=[{"kind": "delta", "data": {"text": "hello"}}, {"kind": "final", "data": {}}],
        final_outcome="success",
        finished_at=datetime.now(UTC).replace(tzinfo=None),
    )
    return sess, artifact


async def test_persist_verdict_writes_row_and_mirrors_score(db_session, workspace, identity):
    _sess, artifact = await _seed_artifact(db_session, workspace, identity)

    verdict = await judge_svc.persist_verdict(
        db_session,
        workspace_id=workspace.id,
        artifact_id=artifact.id,
        score=1,
        confidence=0.91,
        rationale="Solid run. Final answer matches user request.",
        process_notes=["one tool call", "no retries"],
        judged_by_model="openai:gpt-4o",
        latency_ms=1234,
    )
    assert verdict.score == 1
    assert verdict.judged_by_model == "openai:gpt-4o"
    assert verdict.degraded is False

    refreshed = await artifact_svc.get_artifact_by_id(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id
    )
    assert refreshed.judge_score == pytest.approx(1.0)


async def test_persist_verdict_is_idempotent_on_artifact(db_session, workspace, identity):
    _sess, artifact = await _seed_artifact(db_session, workspace, identity)

    await judge_svc.persist_verdict(
        db_session,
        workspace_id=workspace.id,
        artifact_id=artifact.id,
        score=0,
        confidence=0.5,
        rationale="partial",
        judged_by_model="m1",
    )
    second = await judge_svc.persist_verdict(
        db_session,
        workspace_id=workspace.id,
        artifact_id=artifact.id,
        score=-1,
        confidence=0.8,
        rationale="actually a failure on review",
        judged_by_model="m2",
    )
    assert second.score == -1
    assert second.judged_by_model == "m2"
    refreshed = await artifact_svc.get_artifact_by_id(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id
    )
    assert refreshed.judge_score == pytest.approx(-1.0)


async def test_persist_verdict_rejects_out_of_range(db_session, workspace, identity):
    _sess, artifact = await _seed_artifact(db_session, workspace, identity)
    with pytest.raises(ValueError):
        await judge_svc.persist_verdict(
            db_session,
            workspace_id=workspace.id,
            artifact_id=artifact.id,
            score=2,
            confidence=1.0,
            rationale="bogus",
        )


async def test_clear_verdict_resets_score_and_drops_row(db_session, workspace, identity):
    _sess, artifact = await _seed_artifact(db_session, workspace, identity)
    await judge_svc.persist_verdict(
        db_session,
        workspace_id=workspace.id,
        artifact_id=artifact.id,
        score=1,
        confidence=0.9,
        rationale="ok",
    )
    refreshed = await artifact_svc.get_artifact_by_id(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id
    )
    assert refreshed.judge_score is not None

    deleted = await judge_svc.clear_verdict(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id
    )
    assert deleted is True

    refreshed = await artifact_svc.get_artifact_by_id(
        db_session, workspace_id=workspace.id, artifact_id=artifact.id
    )
    assert refreshed.judge_score is None
    assert (
        await judge_svc.get_verdict(db_session, workspace_id=workspace.id, artifact_id=artifact.id)
        is None
    )


async def test_session_summary_buckets(db_session, workspace, identity):
    sess, artifact_a = await _seed_artifact(db_session, workspace, identity)

    artifact_b = await artifact_svc.capture_artifact(
        db_session,
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="another",
        events=[{"kind": "final", "data": {}}],
        final_outcome="success",
        finished_at=datetime.now(UTC).replace(tzinfo=None),
    )

    await judge_svc.persist_verdict(
        db_session,
        workspace_id=workspace.id,
        artifact_id=artifact_a.id,
        score=1,
        confidence=0.9,
        rationale="ok",
    )

    counts = await judge_svc.session_summary(
        db_session, workspace_id=workspace.id, session_id=sess.id
    )
    assert counts["total_artifacts"] == 2
    assert counts["success"] == 1
    assert counts["unjudged"] == 1
    # b not yet judged
    assert artifact_b.judge_score is None
