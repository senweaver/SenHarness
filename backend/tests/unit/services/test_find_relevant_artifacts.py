"""Unit: ``skill_verifier.find_relevant_artifacts`` (M2.4).

Covers all three matching heuristics, cross-workspace isolation, and
the empty-history fast path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.session_artifact import SessionArtifactRepository
from app.repositories.skills import SkillPackRepository
from app.services import session as session_svc
from app.services import skill_verifier as verifier_svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id, slug=None):
    return await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=slug or f"sk-{uuid.uuid4().hex[:6]}",
        name="Test pack",
        description="x",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
    )


async def _make_artifact(
    db,
    *,
    workspace,
    identity,
    injected_pack_ids=None,
    invoked_tools=None,
    turns_text=None,
    finished_offset_minutes=0,
):
    sess = await session_svc.create_session(
        db,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    repo = SessionArtifactRepository(db)
    finished = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=finished_offset_minutes
    )
    turns_payload = []
    if turns_text:
        turns_payload = [
            {"role": "user", "iteration": 0, "text": turns_text},
            {"role": "assistant", "iteration": 1, "text": "ok"},
        ]
    row = await repo.create(
        workspace_id=workspace.id,
        run_id=uuid.uuid4(),
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text_hash="0" * 64,
        turns_json=turns_payload,
        injected_skill_pack_ids=[str(p) for p in (injected_pack_ids or [])],
        invoked_tools=list(invoked_tools or []),
        iteration_count=1,
        final_outcome="success",
        error_kind=None,
        goal_alignment_avg=None,
        finished_at=finished,
    )
    return row


async def test_find_returns_empty_when_no_artifacts(
    db_session, workspace
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    rows = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        pack_slug=pack.slug,
        limit=10,
    )
    assert rows == []


async def test_find_matches_injected_pack_id(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    art = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        injected_pack_ids=[pack.id],
    )
    rows = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        pack_slug=pack.slug,
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0].id == art.id


async def test_find_matches_invoked_tool_slug(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(
        db_session, workspace_id=workspace.id, slug="my-skill"
    )
    art = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        invoked_tools=["my-skill", "other"],
    )
    rows = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        pack_slug=pack.slug,
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0].id == art.id


async def test_find_matches_turns_json_substring(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(
        db_session, workspace_id=workspace.id, slug="zoom-search"
    )
    art = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        turns_text="please use zoom-search to look this up",
    )
    rows = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        pack_slug=pack.slug,
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0].id == art.id


async def test_find_isolates_across_workspaces(
    db_session, workspace, identity
) -> None:
    from app.services import workspace as ws_svc

    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other {uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    pack_a = await _make_pack(
        db_session, workspace_id=workspace.id, slug="shared-slug"
    )
    pack_b = await _make_pack(
        db_session, workspace_id=other_ws.id, slug="shared-slug"
    )
    await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        injected_pack_ids=[pack_a.id],
    )
    # Workspace A query must NOT see workspace B's artifact even though
    # the slug matches; the workspace_id predicate is the only thing
    # standing between us and a cross-tenant leak.
    rows_a = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack_a.id,
        pack_slug=pack_a.slug,
        limit=10,
    )
    assert len(rows_a) == 1
    rows_b = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=other_ws.id,
        pack_id=pack_b.id,
        pack_slug=pack_b.slug,
        limit=10,
    )
    assert rows_b == []


async def test_find_orders_by_finished_at_desc(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    older = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        injected_pack_ids=[pack.id],
        finished_offset_minutes=120,
    )
    newer = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        injected_pack_ids=[pack.id],
        finished_offset_minutes=10,
    )
    rows = await verifier_svc.find_relevant_artifacts(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        pack_slug=pack.slug,
        limit=10,
    )
    assert [r.id for r in rows] == [newer.id, older.id]
