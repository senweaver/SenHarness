"""Unit: ``evolver_workflow.build_drain_summary`` (M2.3).

Pure-ish read-side checks against a real Postgres engine; the Score
distribution + error_kind aggregation is what both engine modes
share. No aux LLM is called from these cases.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.schemas.session_artifact import ArtifactOutcome
from app.services import evolver_workflow as svc

pytestmark = pytest.mark.asyncio


async def _seed_artifact(
    db,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    judge_score: float | None,
    error_kind: str | None,
    invoked_tools: list[str] | None,
    finished_at,
    outcome: ArtifactOutcome = ArtifactOutcome.ERROR,
) -> SessionArtifact:
    art = SessionArtifact(
        workspace_id=workspace_id,
        run_id=uuid.uuid4(),
        session_id=session_id,
        agent_id=None,
        identity_id=None,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=invoked_tools or [],
        iteration_count=1,
        final_outcome=outcome.value,
        error_kind=error_kind,
        judge_score=judge_score,
        goal_alignment_avg=None,
        finished_at=finished_at,
    )
    db.add(art)
    await db.flush([art])
    return art


async def _ensure_session(db, *, workspace_id, identity_id) -> uuid.UUID:
    """Minimal session row so the FK on SessionArtifact.session_id resolves."""
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db,
        workspace_id=workspace_id,
        owner_identity_id=identity_id,
        title="drain test",
    )
    await db.flush()
    return sess.id


async def test_empty_workspace_returns_empty_summary(db_session, workspace, identity):
    _ = identity
    summary = await svc.build_drain_summary(
        db_session,
        workspace_id=workspace.id,
        since=utcnow_naive() - timedelta(days=7),
    )
    assert summary.is_empty()
    assert summary.artifact_count == 0
    assert summary.score_distribution == {}
    assert summary.common_error_kinds == []
    assert summary.common_invoked_tools == []
    assert summary.sample_artifact_ids == []


async def test_failing_and_passing_distribution(db_session, workspace, identity):
    workspace_id = workspace.id
    session_id = await _ensure_session(
        db_session, workspace_id=workspace_id, identity_id=identity.id
    )
    now = utcnow_naive()

    for _ in range(5):
        await _seed_artifact(
            db_session,
            workspace_id=workspace_id,
            session_id=session_id,
            judge_score=-1.0,
            error_kind="hallucination",
            invoked_tools=["web_search"],
            finished_at=now,
        )
    for _ in range(3):
        await _seed_artifact(
            db_session,
            workspace_id=workspace_id,
            session_id=session_id,
            judge_score=1.0,
            error_kind=None,
            invoked_tools=["calculator"],
            finished_at=now,
            outcome=ArtifactOutcome.SUCCESS,
        )
    await db_session.commit()

    summary = await svc.build_drain_summary(
        db_session,
        workspace_id=workspace_id,
        since=now - timedelta(days=1),
    )
    assert summary.artifact_count == 5
    assert summary.score_distribution == {-1: 5}
    assert summary.common_error_kinds == [("hallucination", 5)]
    assert summary.common_invoked_tools == [("web_search", 5)]
    assert len(summary.sample_artifact_ids) == 5


async def test_error_kind_grouping_sorted_by_frequency(db_session, workspace, identity):
    workspace_id = workspace.id
    session_id = await _ensure_session(
        db_session, workspace_id=workspace_id, identity_id=identity.id
    )
    now = utcnow_naive()

    plan = [
        ("hallucination", 4, ["web_search"]),
        ("timeout", 2, ["shell"]),
        ("schema_violation", 1, ["calculator"]),
    ]
    for kind, count, tools in plan:
        for _ in range(count):
            await _seed_artifact(
                db_session,
                workspace_id=workspace_id,
                session_id=session_id,
                judge_score=-1.0,
                error_kind=kind,
                invoked_tools=tools,
                finished_at=now,
            )
    await db_session.commit()

    summary = await svc.build_drain_summary(
        db_session,
        workspace_id=workspace_id,
        since=now - timedelta(days=1),
    )

    assert summary.artifact_count == 7
    assert summary.common_error_kinds[0] == ("hallucination", 4)
    assert summary.common_error_kinds[1] == ("timeout", 2)
    assert summary.common_error_kinds[2] == ("schema_violation", 1)
    assert summary.common_invoked_tools[0] == ("web_search", 4)


async def test_judge_score_max_filter_excludes_partials(db_session, workspace, identity):
    """A custom judge_score_max keeps the bound exclusive on the upper end."""
    workspace_id = workspace.id
    session_id = await _ensure_session(
        db_session, workspace_id=workspace_id, identity_id=identity.id
    )
    now = utcnow_naive()

    await _seed_artifact(
        db_session,
        workspace_id=workspace_id,
        session_id=session_id,
        judge_score=-1.0,
        error_kind="hallucination",
        invoked_tools=["web_search"],
        finished_at=now,
    )
    await _seed_artifact(
        db_session,
        workspace_id=workspace_id,
        session_id=session_id,
        judge_score=0.0,
        error_kind=None,
        invoked_tools=["calculator"],
        finished_at=now,
        outcome=ArtifactOutcome.PARTIAL,
    )
    await db_session.commit()

    summary = await svc.build_drain_summary(
        db_session,
        workspace_id=workspace_id,
        since=now - timedelta(days=1),
        judge_score_max=0.0,  # default — only judge_score < 0 in the bucket
    )
    assert summary.artifact_count == 1
