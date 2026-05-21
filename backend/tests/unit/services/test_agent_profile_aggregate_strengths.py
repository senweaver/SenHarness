"""Unit: ``agent_profile.aggregate_strengths`` (M3.4).

Pure-ish read-side checks against a real Postgres engine. Drives the
toolset / skill_categories / domains buckets without ever calling
the aux LLM (the failure-modes pass — which does — has its own
test file).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.models.skills import SkillPack, SkillPackSource
from app.schemas.session_artifact import ArtifactOutcome
from app.services import agent_profile as svc

pytestmark = pytest.mark.asyncio


async def _ensure_session(db, *, workspace_id, identity_id) -> uuid.UUID:
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db,
        workspace_id=workspace_id,
        owner_identity_id=identity_id,
        title="profile test",
    )
    await db.flush()
    return sess.id


async def _seed_artifact(
    db,
    *,
    workspace_id,
    session_id,
    agent_id,
    judge_score,
    invoked_tools,
    injected_skill_pack_ids=None,
    finished_at=None,
    outcome=ArtifactOutcome.SUCCESS,
    error_kind=None,
) -> SessionArtifact:
    art = SessionArtifact(
        workspace_id=workspace_id,
        run_id=uuid.uuid4(),
        session_id=session_id,
        agent_id=agent_id,
        identity_id=None,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=injected_skill_pack_ids or [],
        invoked_tools=invoked_tools or [],
        iteration_count=1,
        final_outcome=outcome.value,
        error_kind=error_kind,
        judge_score=judge_score,
        goal_alignment_avg=None,
        finished_at=finished_at or utcnow_naive(),
    )
    db.add(art)
    await db.flush([art])
    return art


async def _make_pack(db, *, workspace_id, slug, tags=None) -> SkillPack:
    pack = SkillPack(
        workspace_id=workspace_id,
        slug=slug,
        name=slug.title(),
        version="0.1.0",
        manifest_json={"tags": list(tags)} if tags else {},
        metadata_json={},
        source=SkillPackSource.WORKSPACE,
    )
    db.add(pack)
    await db.flush([pack])
    return pack


async def test_empty_returns_empty_buckets(db_session, workspace, agent):
    _ = agent
    since = utcnow_naive() - timedelta(days=30)
    out = await svc.aggregate_strengths(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=since,
    )
    assert out["toolsets"] == []
    assert out["skill_categories"] == []
    assert out["domains"] == []
    assert out["sample_artifact_count"] == 0


async def test_toolsets_count_and_avg_score(db_session, workspace, agent, identity):
    sess = await _ensure_session(
        db_session, workspace_id=workspace.id, identity_id=identity.id
    )
    now = utcnow_naive()
    await _seed_artifact(
        db_session,
        workspace_id=workspace.id,
        session_id=sess,
        agent_id=agent.id,
        judge_score=1.0,
        invoked_tools=["browser", "search"],
        finished_at=now,
    )
    await _seed_artifact(
        db_session,
        workspace_id=workspace.id,
        session_id=sess,
        agent_id=agent.id,
        judge_score=0.0,
        invoked_tools=["browser"],
        finished_at=now - timedelta(hours=1),
    )
    # Failed run — must be excluded from strengths buckets.
    await _seed_artifact(
        db_session,
        workspace_id=workspace.id,
        session_id=sess,
        agent_id=agent.id,
        judge_score=-1.0,
        invoked_tools=["browser"],
        finished_at=now - timedelta(hours=2),
        outcome=ArtifactOutcome.ERROR,
        error_kind="parse_error",
    )

    since = now - timedelta(days=30)
    out = await svc.aggregate_strengths(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=since,
    )
    assert out["sample_artifact_count"] == 2
    by_name = {row["name"]: row for row in out["toolsets"]}
    assert by_name["browser"]["use_count"] == 2
    assert by_name["browser"]["effectiveness_avg"] == pytest.approx(0.5, abs=1e-3)
    assert by_name["search"]["use_count"] == 1
    assert by_name["search"]["effectiveness_avg"] == pytest.approx(1.0)


async def test_skill_categories_bucket_by_manifest_tags(
    db_session, workspace, agent, identity
):
    pack_a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        slug="pack-a",
        tags=["data-analysis", "marketing"],
    )
    pack_b = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        slug="pack-b",
    )

    sess = await _ensure_session(
        db_session, workspace_id=workspace.id, identity_id=identity.id
    )
    now = utcnow_naive()
    await _seed_artifact(
        db_session,
        workspace_id=workspace.id,
        session_id=sess,
        agent_id=agent.id,
        judge_score=1.0,
        invoked_tools=[],
        injected_skill_pack_ids=[str(pack_a.id), str(pack_b.id)],
        finished_at=now,
    )

    since = now - timedelta(days=30)
    out = await svc.aggregate_strengths(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=since,
    )
    by_cat = {row["category"]: row["use_count"] for row in out["skill_categories"]}
    assert by_cat["data-analysis"] == 1
    assert by_cat["marketing"] == 1
    assert by_cat["general"] == 1


async def test_other_agent_artifacts_are_filtered_out(
    db_session, workspace, agent, identity
):
    """An artifact owned by a different agent in the same workspace
    must not contribute to the buckets — the unique ``agent_id``
    filter is what makes the row 1:1 with agents.
    """
    other_agent = uuid.uuid4()
    sess = await _ensure_session(
        db_session, workspace_id=workspace.id, identity_id=identity.id
    )
    now = utcnow_naive()
    await _seed_artifact(
        db_session,
        workspace_id=workspace.id,
        session_id=sess,
        agent_id=agent.id,
        judge_score=1.0,
        invoked_tools=["browser"],
        finished_at=now,
    )
    # Artifact attributed to no agent (or another agent uuid) is
    # invisible to this aggregation pass.
    art = SessionArtifact(
        workspace_id=workspace.id,
        run_id=uuid.uuid4(),
        session_id=sess,
        agent_id=None,
        identity_id=None,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=["other-tool"],
        iteration_count=1,
        final_outcome="success",
        error_kind=None,
        judge_score=1.0,
        goal_alignment_avg=None,
        finished_at=now,
    )
    db_session.add(art)
    await db_session.flush([art])

    _ = other_agent  # signal intent without exercising the negative path twice

    since = now - timedelta(days=30)
    out = await svc.aggregate_strengths(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=since,
    )
    names = {row["name"] for row in out["toolsets"]}
    assert "browser" in names
    assert "other-tool" not in names
