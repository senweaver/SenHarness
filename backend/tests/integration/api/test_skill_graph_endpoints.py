"""Integration: M4.2 ``/skills/packs/{pack_id}/graph`` + ``/lineage``.

Covers:

* Anonymous / cross-workspace requests get 401 / 404.
* Happy path: a 2-hop lineage chain returns nodes + edges.
* ``/lineage`` returns just one-step incoming + outgoing edges.
* ``depth`` query bound is enforced at the validation layer.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.skill_lineage_edge import SkillLineageEdgeKind
from app.db.session import get_session_factory
from app.repositories.skill_lineage_edge import SkillLineageEdgeRepository

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"sg-{uuid.uuid4().hex[:8]}@example.com"
    password = "skill-graph-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Graph Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = r.json()["access_token"]
    workspace = body.get("workspace") or {}
    ws_id = workspace.get("id")
    headers = {"Authorization": f"Bearer {token}"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _create_pack(async_client, headers, *, slug=None) -> str:
    payload = {
        "slug": slug or f"sk-{uuid.uuid4().hex[:8]}",
        "name": "Test pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": "---\nname: x\ndescription: y\n---\n\nbody",
    }
    r = await async_client.post(
        "/api/v1/skills/packs", headers=headers, json=payload
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _seed_edge(
    *,
    workspace_id,
    parent_id,
    child_id,
    kind: SkillLineageEdgeKind,
    hub_pack_slug: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        await SkillLineageEdgeRepository(db).upsert_edge(
            workspace_id=uuid.UUID(workspace_id),
            parent_pack_id=uuid.UUID(parent_id) if parent_id else None,
            child_pack_id=uuid.UUID(child_id),
            edge_kind=kind,
            hub_pack_slug=hub_pack_slug,
        )
        await db.commit()


async def test_graph_endpoint_returns_two_hop_neighbourhood(async_client):
    headers, ws_id = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers)
    b = await _create_pack(async_client, headers)
    c = await _create_pack(async_client, headers)
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=a,
        child_id=b,
        kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=b,
        child_id=c,
        kind=SkillLineageEdgeKind.DERIVED_FROM,
    )

    r = await async_client.get(
        f"/api/v1/skills/packs/{a}/graph?depth=2", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["focus_pack_id"] == a
    assert body["depth"] == 2
    node_ids = {n["node_id"] for n in body["nodes"]}
    assert {a, b, c} <= node_ids
    edge_pairs = {(e["parent_id"], e["child_id"]) for e in body["edges"]}
    assert (a, b) in edge_pairs and (b, c) in edge_pairs


async def test_graph_endpoint_depth_one_does_not_reach_grandchild(async_client):
    headers, ws_id = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers)
    b = await _create_pack(async_client, headers)
    c = await _create_pack(async_client, headers)
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=a,
        child_id=b,
        kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=b,
        child_id=c,
        kind=SkillLineageEdgeKind.DERIVED_FROM,
    )

    r = await async_client.get(
        f"/api/v1/skills/packs/{a}/graph?depth=1", headers=headers
    )
    body = r.json()
    node_ids = {n["node_id"] for n in body["nodes"]}
    assert a in node_ids and b in node_ids
    assert c not in node_ids


async def test_graph_endpoint_rejects_oversized_depth(async_client):
    headers, _ = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers)
    r = await async_client.get(
        f"/api/v1/skills/packs/{a}/graph?depth=42", headers=headers
    )
    assert r.status_code == 422


async def test_graph_endpoint_cross_workspace_returns_404(async_client):
    headers_a, _ = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers_a)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/skills/packs/{a}/graph", headers=headers_b
    )
    assert r.status_code == 404


async def test_lineage_endpoint_returns_one_step(async_client):
    headers, ws_id = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers)
    b = await _create_pack(async_client, headers)
    c = await _create_pack(async_client, headers)
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=a,
        child_id=b,
        kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=b,
        child_id=c,
        kind=SkillLineageEdgeKind.DERIVED_FROM,
    )

    r = await async_client.get(
        f"/api/v1/skills/packs/{b}/lineage", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["focus_pack_id"] == b
    incoming_parents = {e["parent_pack_id"] for e in body["incoming"]}
    outgoing_children = {e["child_pack_id"] for e in body["outgoing"]}
    assert a in incoming_parents
    assert c in outgoing_children


async def test_graph_endpoint_synthesises_hub_external_node(async_client):
    headers, ws_id = await _bootstrap(async_client)
    a = await _create_pack(async_client, headers)
    await _seed_edge(
        workspace_id=ws_id,
        parent_id=None,
        child_id=a,
        kind=SkillLineageEdgeKind.PULLED_FROM_HUB,
        hub_pack_slug="from-hub",
    )

    r = await async_client.get(
        f"/api/v1/skills/packs/{a}/graph?depth=1", headers=headers
    )
    body = r.json()
    external = [n for n in body["nodes"] if n["is_external"]]
    assert len(external) == 1
    assert external[0]["node_id"] == "hub:from-hub"
    assert external[0]["pack_id"] is None


async def test_graph_requires_authentication(async_client):
    r = await async_client.get(
        f"/api/v1/skills/packs/{uuid.uuid4()}/graph"
    )
    assert r.status_code == 401
