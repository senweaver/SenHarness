"""Unit tests for :class:`SkillLineageEdgeRepository` (M4.2).

Covers the unique constraint, list_for_pack, list_outgoing /
list_incoming, and the run-id merge semantics on
:meth:`upsert_edge`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.skill_lineage_edge import SkillLineageEdgeKind
from app.repositories.skill_lineage_edge import SkillLineageEdgeRepository
from app.repositories.skills import SkillPackRepository

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id, identity_id, slug):
    return await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=slug,
        name=slug,
        description="",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=identity_id,
    )


async def test_upsert_edge_is_idempotent_and_merges_run_ids(
    db_session, workspace, identity
):
    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    b = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"b-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    repo = SkillLineageEdgeRepository(db_session)
    first = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
        derived_from_run_ids=["run-1"],
        metadata_json={"actor": "evolver"},
    )
    second = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
        derived_from_run_ids=["run-2"],
        metadata_json={"verdict": "ok"},
    )
    assert first.id == second.id
    assert sorted(second.derived_from_run_ids) == ["run-1", "run-2"]
    assert second.metadata_json["actor"] == "evolver"
    assert second.metadata_json["verdict"] == "ok"


async def test_unique_constraint_rejects_duplicate_via_create(
    db_session, workspace, identity
):
    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    b = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"b-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    repo = SkillLineageEdgeRepository(db_session)
    await repo.create(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await db_session.flush()
    with pytest.raises(IntegrityError):
        await repo.create(
            workspace_id=workspace.id,
            parent_pack_id=a.id,
            child_pack_id=b.id,
            edge_kind=SkillLineageEdgeKind.SUPERSEDES,
        )
        await db_session.flush()
    await db_session.rollback()


async def test_list_for_pack_returns_both_directions(
    db_session, workspace, identity
):
    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    b = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"b-{uuid.uuid4().hex[:6]}",
    )
    c = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"c-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    repo = SkillLineageEdgeRepository(db_session)
    e1 = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    e2 = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=c.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
    )
    await db_session.flush()

    around_b = list(
        await repo.list_for_pack(workspace_id=workspace.id, pack_id=b.id)
    )
    ids = {e.id for e in around_b}
    assert e1.id in ids and e2.id in ids

    incoming = list(
        await repo.list_incoming(workspace_id=workspace.id, pack_id=b.id)
    )
    outgoing = list(
        await repo.list_outgoing(workspace_id=workspace.id, pack_id=b.id)
    )
    assert {e.id for e in incoming} == {e1.id, e2.id}
    assert outgoing == []


async def test_pulled_from_hub_allows_null_parent(db_session, workspace, identity):
    child = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"c-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()
    repo = SkillLineageEdgeRepository(db_session)
    e = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=None,
        child_pack_id=child.id,
        edge_kind=SkillLineageEdgeKind.PULLED_FROM_HUB,
        hub_pack_slug="code-review",
    )
    assert e.parent_pack_id is None
    assert e.hub_pack_slug == "code-review"

    # Re-upsert is idempotent and updates the slug only when supplied.
    e2 = await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=None,
        child_pack_id=child.id,
        edge_kind=SkillLineageEdgeKind.PULLED_FROM_HUB,
        hub_pack_slug="code-review",
    )
    assert e2.id == e.id
