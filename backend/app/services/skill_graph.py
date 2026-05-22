"""Skill knowledge graph service (M4.2).

Builds the BFS-bounded lineage graph that the
``/skills/packs/{pack_id}/graph`` endpoint returns. Two design points
that distinguish this from a generic graph traversal:

* **Lineage is truth, not similarity.** Edges are read from the
  :class:`~app.db.models.skill_lineage_edge.SkillLineageEdge` relation
  table, never from pgvector. The four edge kinds are enumerated in
  :class:`~app.db.models.skill_lineage_edge.SkillLineageEdgeKind`.
* **Cross-workspace isolation.** Every query is scoped to the caller
  workspace. Hub-pull edges that reference a slug published from a
  sibling tenant are rendered as a sanitized placeholder node: a
  synthetic id ``"hub:<slug>"``, no source workspace metadata, and
  the ``is_external`` flag set so the UI styles the node as
  "From Hub".

The BFS is depth-bounded (``max_depth`` ≤ :data:`MAX_DEPTH`) and
node-bounded (:data:`MAX_NODES`). When the cap is hit before the
depth budget is exhausted the response carries ``truncated=True`` so
the caller can prompt the user to drill in.

Edge-write helpers
------------------

:func:`record_lineage_edge_for_propose` and
:func:`record_lineage_edge_for_pull` are the two public sinks the
M2.1 propose path and the M3.3 pull path call to land their edges.
They live here so M4.2 ships standalone without touching either of
those modules; M2.1 / M3.3 follow-ups simply ``await`` the helper at
the appropriate point in their existing flow.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skill_lineage_edge import (
    SkillLineageEdge,
    SkillLineageEdgeKind,
)
from app.db.models.skills import SkillPack
from app.repositories.skill_lineage_edge import SkillLineageEdgeRepository
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)

__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "GraphEdge",
    "GraphNode",
    "SkillGraph",
    "build_skill_graph",
    "list_one_step_lineage",
    "record_lineage_edge_for_propose",
    "record_lineage_edge_for_pull",
    "record_lineage_edge_for_supersede",
]


MAX_DEPTH: int = 3
"""Largest depth a caller can request without rejection.

The endpoint clamps anything bigger to :data:`MAX_DEPTH` rather than
raising — the cap is defensive against accidental graph fan-out, not
a wire contract violation.
"""

MAX_NODES: int = 200
"""Hard cap on the BFS node frontier. When the cap fires before the
depth budget is exhausted the response sets ``truncated=True`` so the
UI can prompt for a smaller depth.
"""


# ── Public DTOs (service-internal) ───────────────────────────────
@dataclass
class GraphNode:
    pack_id: uuid.UUID | None
    slug: str
    name: str
    state: str | None = None
    pinned: bool = False
    enabled: bool = True
    is_external: bool = False
    is_focus: bool = False

    @property
    def node_id(self) -> str:
        if self.pack_id is None:
            return f"hub:{self.slug}"
        return str(self.pack_id)


@dataclass
class GraphEdge:
    parent_id: str
    child_id: str
    kind: SkillLineageEdgeKind
    derived_from_run_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: object | None = None  # datetime — kept loose so dataclass stays simple


@dataclass
class SkillGraph:
    focus_pack_id: uuid.UUID
    depth: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False


# ── Builder ─────────────────────────────────────────────────────
async def build_skill_graph(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    focus_pack_id: uuid.UUID,
    depth: int = 2,
) -> SkillGraph:
    """Build the BFS neighbourhood graph rooted at ``focus_pack_id``.

    The walk is undirected over the relation table — any edge that
    touches a frontier pack on either end pulls the other end into
    the next layer. This keeps the result symmetric: looking at a
    derived child surfaces the ancestor it inherited from, and vice
    versa.

    Cross-workspace isolation is enforced two ways:

    1. The pack lookup goes through :class:`SkillPackRepository.get`
       and the result is rejected if ``workspace_id`` doesn't match.
    2. Hub-pull edges where the source pack lives on the hub never
       contribute a real ``SkillPack`` row — only a synthetic
       ``hub:<slug>`` placeholder. No sibling-tenant metadata can
       leak through this code path because it never reads from
       another workspace.

    Returns an empty graph (just the focus node) when no edges
    touch ``focus_pack_id``.
    """
    bounded_depth = max(0, min(int(depth or 0), MAX_DEPTH))

    pack_repo = SkillPackRepository(db)
    edge_repo = SkillLineageEdgeRepository(db)

    focus = await pack_repo.get(focus_pack_id, include_deleted=True)
    if focus is None or focus.workspace_id != workspace_id:
        return SkillGraph(
            focus_pack_id=focus_pack_id,
            depth=bounded_depth,
            nodes=[],
            edges=[],
            truncated=False,
        )

    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    pack_cache: dict[uuid.UUID, SkillPack] = {focus.id: focus}

    focus_node = _node_from_pack(focus, is_focus=True)
    nodes[focus_node.node_id] = focus_node

    truncated = False

    # ``frontier`` is the set of *workspace pack ids* whose edges we
    # still need to expand. Hub placeholder ids never re-enter the
    # frontier — they're terminal because the hub side isn't loaded.
    frontier: set[uuid.UUID] = {focus.id}
    visited: set[uuid.UUID] = set()

    for _layer in range(bounded_depth):
        if not frontier:
            break
        layer_edges = await edge_repo.list_for_packs(
            workspace_id=workspace_id, pack_ids=tuple(frontier)
        )
        next_frontier: set[uuid.UUID] = set()

        # Pre-load every workspace-side neighbour pack in one query.
        neighbour_ids: set[uuid.UUID] = set()
        for edge in layer_edges:
            if edge.parent_pack_id and edge.parent_pack_id not in pack_cache:
                neighbour_ids.add(edge.parent_pack_id)
            if edge.child_pack_id and edge.child_pack_id not in pack_cache:
                neighbour_ids.add(edge.child_pack_id)
        if neighbour_ids:
            stmt = select(SkillPack).where(
                SkillPack.workspace_id == workspace_id,
                SkillPack.id.in_(neighbour_ids),
            )
            for row in (await db.execute(stmt)).scalars().all():
                pack_cache[row.id] = row

        for edge in layer_edges:
            parent_node, parent_id = _resolve_edge_endpoint(
                edge.parent_pack_id,
                edge=edge,
                pack_cache=pack_cache,
                workspace_id=workspace_id,
                is_parent=True,
            )
            child_node, child_id = _resolve_edge_endpoint(
                edge.child_pack_id,
                edge=edge,
                pack_cache=pack_cache,
                workspace_id=workspace_id,
                is_parent=False,
            )
            if parent_id is None or child_id is None:
                continue

            if parent_node and parent_node.node_id not in nodes:
                if len(nodes) >= MAX_NODES:
                    truncated = True
                    continue
                nodes[parent_node.node_id] = parent_node
            if child_node and child_node.node_id not in nodes:
                if len(nodes) >= MAX_NODES:
                    truncated = True
                    continue
                nodes[child_node.node_id] = child_node

            key = (parent_id, child_id, edge.edge_kind.value)
            if key not in edges:
                edges[key] = GraphEdge(
                    parent_id=parent_id,
                    child_id=child_id,
                    kind=edge.edge_kind,
                    derived_from_run_ids=list(edge.derived_from_run_ids or []),
                    metadata=dict(edge.metadata_json or {}),
                    created_at=edge.created_at,
                )

            for endpoint in (edge.parent_pack_id, edge.child_pack_id):
                if endpoint is not None and endpoint not in visited:
                    next_frontier.add(endpoint)

        visited.update(frontier)
        frontier = next_frontier - visited
        if truncated:
            break

    return SkillGraph(
        focus_pack_id=focus.id,
        depth=bounded_depth,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        truncated=truncated,
    )


async def list_one_step_lineage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
) -> tuple[list[SkillLineageEdge], list[SkillLineageEdge]]:
    """Return ``(incoming, outgoing)`` edges for a single pack.

    Cheaper than :func:`build_skill_graph` — used by the
    ``/lineage`` route and the side-panel in the SkillPack detail
    view. Cross-workspace isolation is enforced by the repository.
    """
    pack_repo = SkillPackRepository(db)
    pack = await pack_repo.get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        return [], []

    edge_repo = SkillLineageEdgeRepository(db)
    incoming = list(await edge_repo.list_incoming(workspace_id=workspace_id, pack_id=pack_id))
    outgoing = list(await edge_repo.list_outgoing(workspace_id=workspace_id, pack_id=pack_id))
    return incoming, outgoing


# ── Edge-write helpers ──────────────────────────────────────────
async def record_lineage_edge_for_propose(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    parent_pack_id: uuid.UUID | None,
    child_pack_id: uuid.UUID,
    supporting_run_ids: Iterable[str] | None,
    actor_identity_id: uuid.UUID | None = None,
    request: object | None = None,
    audit: bool = True,
) -> SkillLineageEdge | None:
    """Record a ``DERIVED_FROM`` edge for an evolver-proposed version.

    M2.1 ``propose_skill_create`` / ``propose_skill_patch`` /
    ``propose_skill_edit`` call this once per candidate version after
    the SkillPackVersion row lands. The edge anchors on
    ``parent_pack_id`` (the pack the proposal patches) — for the
    ``create`` verb where there is no parent yet the helper returns
    ``None`` because a free-floating ``DERIVED_FROM`` edge would carry
    no graph value (the supporting run ids are already on the
    SkillPackVersion row).

    Idempotent: re-calling for the same parent/child collapses run
    ids set-wise into the existing edge so a multi-step evolver run
    that supplements supporting evidence over time gets the union.
    """
    run_ids = sorted({rid for rid in (supporting_run_ids or []) if rid})
    if parent_pack_id is None:
        return None
    if parent_pack_id == child_pack_id and not run_ids:
        return None

    edge = await SkillLineageEdgeRepository(db).upsert_edge(
        workspace_id=workspace_id,
        parent_pack_id=parent_pack_id,
        child_pack_id=child_pack_id,
        edge_kind=SkillLineageEdgeKind.DERIVED_FROM,
        derived_from_run_ids=run_ids,
        metadata_json={"actor": "evolver"} if run_ids else {},
    )
    if audit:
        await audit_svc.record(
            db,
            action="skill_lineage.edge_created",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=child_pack_id,
            summary=(
                f"derived_from edge: {parent_pack_id} → {child_pack_id} ({len(run_ids)} run(s))"
            ),
            metadata={
                "edge_kind": SkillLineageEdgeKind.DERIVED_FROM.value,
                "parent_pack_id": str(parent_pack_id),
                "child_pack_id": str(child_pack_id),
                "supporting_run_ids": run_ids,
            },
            request=request,
        )
    return edge


async def record_lineage_edge_for_pull(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    local_pack_id: uuid.UUID,
    hub_pack_id: uuid.UUID,
    hub_pack_slug: str,
    actor_identity_id: uuid.UUID | None = None,
    request: object | None = None,
    audit: bool = True,
) -> SkillLineageEdge:
    """Record a ``PULLED_FROM_HUB`` edge for a hub-pulled SkillPack.

    The edge has ``parent_pack_id=NULL`` because the hub source pack
    isn't a workspace SkillPack. ``hub_pack_slug`` carries the slug
    so the M4.2 graph view can render an external placeholder node
    without exposing source-workspace metadata.
    """
    edge = await SkillLineageEdgeRepository(db).upsert_edge(
        workspace_id=workspace_id,
        parent_pack_id=None,
        child_pack_id=local_pack_id,
        edge_kind=SkillLineageEdgeKind.PULLED_FROM_HUB,
        hub_pack_slug=hub_pack_slug,
        metadata_json={"hub_pack_id": str(hub_pack_id)},
    )
    if audit:
        await audit_svc.record(
            db,
            action="skill_lineage.edge_created",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=local_pack_id,
            summary=(f"pulled_from_hub edge: hub:{hub_pack_slug} → {local_pack_id}"),
            metadata={
                "edge_kind": SkillLineageEdgeKind.PULLED_FROM_HUB.value,
                "hub_pack_id": str(hub_pack_id),
                "hub_pack_slug": hub_pack_slug,
                "local_pack_id": str(local_pack_id),
            },
            request=request,
        )
    return edge


async def record_lineage_edge_for_supersede(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    old_pack_id: uuid.UUID,
    new_pack_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None = None,
    request: object | None = None,
    audit: bool = True,
) -> SkillLineageEdge:
    """Record a ``SUPERSEDES`` edge.

    Walks parent → child as ``old → new`` so a BFS from the new pack
    reaches the old pack via the incoming edge (and vice versa).
    Idempotent — recalling with the same pair is a no-op.
    """
    edge = await SkillLineageEdgeRepository(db).upsert_edge(
        workspace_id=workspace_id,
        parent_pack_id=old_pack_id,
        child_pack_id=new_pack_id,
        edge_kind=SkillLineageEdgeKind.SUPERSEDES,
    )
    if audit:
        await audit_svc.record(
            db,
            action="skill_lineage.edge_created",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=new_pack_id,
            summary=f"supersedes edge: {old_pack_id} → {new_pack_id}",
            metadata={
                "edge_kind": SkillLineageEdgeKind.SUPERSEDES.value,
                "old_pack_id": str(old_pack_id),
                "new_pack_id": str(new_pack_id),
            },
            request=request,
        )
    return edge


# ── Internal helpers ────────────────────────────────────────────
def _node_from_pack(pack: SkillPack, *, is_focus: bool = False) -> GraphNode:
    return GraphNode(
        pack_id=pack.id,
        slug=pack.slug,
        name=pack.name,
        state=pack.state.value if pack.state is not None else None,
        pinned=bool(pack.pinned),
        enabled=bool(pack.enabled),
        is_external=False,
        is_focus=is_focus,
    )


def _resolve_edge_endpoint(
    pack_id: uuid.UUID | None,
    *,
    edge: SkillLineageEdge,
    pack_cache: dict[uuid.UUID, SkillPack],
    workspace_id: uuid.UUID,
    is_parent: bool,
) -> tuple[GraphNode | None, str | None]:
    """Resolve one endpoint of a lineage edge into a graph node id.

    Returns a ``(node, node_id)`` tuple. ``node`` is ``None`` when the
    endpoint is already known (the caller will reuse the existing
    node) and ``node_id`` is the stable string the graph response
    keys edges by.

    For a hub-pull edge on the parent side we synthesise an external
    placeholder node — the hub source pack isn't loaded into
    ``pack_cache`` because it lives outside this workspace.
    """
    if pack_id is None:
        if not is_parent:
            return None, None
        if edge.edge_kind != SkillLineageEdgeKind.PULLED_FROM_HUB:
            return None, None
        slug = edge.hub_pack_slug or "unknown"
        node = GraphNode(
            pack_id=None,
            slug=slug,
            name=f"hub:{slug}",
            state=None,
            pinned=False,
            enabled=True,
            is_external=True,
            is_focus=False,
        )
        return node, node.node_id

    pack = pack_cache.get(pack_id)
    if pack is None or pack.workspace_id != workspace_id:
        return None, None
    return _node_from_pack(pack), str(pack.id)
