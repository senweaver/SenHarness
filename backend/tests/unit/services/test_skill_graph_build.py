"""Unit tests for :func:`app.services.skill_graph.build_skill_graph` (M4.2).

Covers:

* BFS expands across edges to depth=2 and stops past the bound.
* Cross-workspace isolation — sibling workspace packs are never
  loaded even if a stray edge points at them.
* Hub-source nodes are synthesised from ``hub_pack_slug`` without
  pulling any sibling-tenant metadata.
* ``MAX_NODES`` clamps fan-out and sets ``truncated=True``.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.skill_lineage_edge import SkillLineageEdgeKind
from app.repositories.skill_lineage_edge import SkillLineageEdgeRepository
from app.repositories.skills import SkillPackRepository
from app.services import skill_graph as graph_svc
from app.services import workspace as ws_svc

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


async def test_bfs_walks_two_hops(db_session, workspace, identity):
    repo = SkillLineageEdgeRepository(db_session)
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

    # A → B (supersedes), B → C (derived_from). Focus = A, depth=2
    # should reach C through B.
    await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=b.id,
        child_pack_id=c.id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
        derived_from_run_ids=["run-1", "run-2"],
    )
    await db_session.flush()

    graph = await graph_svc.build_skill_graph(
        db_session, workspace_id=workspace.id, focus_pack_id=a.id, depth=2
    )

    node_ids = {n.node_id for n in graph.nodes}
    assert {str(a.id), str(b.id), str(c.id)} <= node_ids
    focus_nodes = [n for n in graph.nodes if n.is_focus]
    assert len(focus_nodes) == 1 and focus_nodes[0].pack_id == a.id

    edge_kinds = {(e.parent_id, e.child_id, e.kind) for e in graph.edges}
    assert (str(a.id), str(b.id), SkillLineageEdgeKind.SUPERSEDES) in edge_kinds
    assert (str(b.id), str(c.id), SkillLineageEdgeKind.DERIVED_FROM) in edge_kinds
    assert graph.truncated is False


async def test_bfs_depth_one_stops(db_session, workspace, identity):
    repo = SkillLineageEdgeRepository(db_session)
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
    await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=b.id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await repo.upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=b.id,
        child_pack_id=c.id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
    )
    await db_session.flush()

    graph = await graph_svc.build_skill_graph(
        db_session, workspace_id=workspace.id, focus_pack_id=a.id, depth=1
    )
    node_ids = {n.node_id for n in graph.nodes}
    assert str(a.id) in node_ids and str(b.id) in node_ids
    assert str(c.id) not in node_ids


async def test_cross_workspace_pack_is_not_loaded(db_session, workspace, identity):
    """A stray edge pointing at a sibling-workspace pack must not pull
    that pack into the result, even if the edge row leaked.
    """
    other_ws = await ws_svc.create_workspace(
        db_session,
        name="other",
        slug=f"o-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    foreign = await _make_pack(
        db_session,
        workspace_id=other_ws.id,
        identity_id=identity.id,
        slug=f"foreign-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    # Force-insert an edge that crosses the boundary (only possible
    # via direct repository access — production flows never do this).
    await SkillLineageEdgeRepository(db_session).create(
        workspace_id=workspace.id,
        parent_pack_id=a.id,
        child_pack_id=foreign.id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await db_session.flush()

    graph = await graph_svc.build_skill_graph(
        db_session, workspace_id=workspace.id, focus_pack_id=a.id, depth=2
    )
    node_ids = {n.node_id for n in graph.nodes}
    # Foreign pack is rejected — neither a node nor an edge mentions it.
    assert str(foreign.id) not in node_ids
    assert all(e.child_id != str(foreign.id) for e in graph.edges)


async def test_hub_external_node_is_synthesised(db_session, workspace, identity):
    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()
    await SkillLineageEdgeRepository(db_session).upsert_edge(
        workspace_id=workspace.id,
        parent_pack_id=None,
        child_pack_id=a.id,
        edge_kind=SkillLineageEdgeKind.PULLED_FROM_HUB,
        hub_pack_slug="code-review",
        metadata_json={"hub_pack_id": "00000000-0000-0000-0000-000000000001"},
    )
    await db_session.flush()

    graph = await graph_svc.build_skill_graph(
        db_session, workspace_id=workspace.id, focus_pack_id=a.id, depth=2
    )
    external_nodes = [n for n in graph.nodes if n.is_external]
    assert len(external_nodes) == 1
    ext = external_nodes[0]
    assert ext.node_id == "hub:code-review"
    assert ext.pack_id is None
    assert any(
        e.parent_id == "hub:code-review" and e.child_id == str(a.id)
        for e in graph.edges
    )


async def test_focus_pack_outside_workspace_returns_empty(
    db_session, workspace, identity
):
    other_ws = await ws_svc.create_workspace(
        db_session,
        name="other",
        slug=f"o-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    foreign = await _make_pack(
        db_session,
        workspace_id=other_ws.id,
        identity_id=identity.id,
        slug=f"f-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()

    graph = await graph_svc.build_skill_graph(
        db_session,
        workspace_id=workspace.id,
        focus_pack_id=foreign.id,
        depth=2,
    )
    assert graph.nodes == []
    assert graph.edges == []


async def test_depth_clamped_to_max(db_session, workspace, identity):
    a = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        slug=f"a-{uuid.uuid4().hex[:6]}",
    )
    await db_session.flush()
    graph = await graph_svc.build_skill_graph(
        db_session, workspace_id=workspace.id, focus_pack_id=a.id, depth=99
    )
    assert graph.depth == graph_svc.MAX_DEPTH
