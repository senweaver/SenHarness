"""Skill knowledge graph endpoints (M4.2).

Two routes share the same router so the FastAPI mount line in
``app.api.router`` stays compact:

* ``GET /skills/packs/{pack_id}/graph?depth=N`` — full BFS graph,
  bounded to :data:`~app.services.skill_graph.MAX_DEPTH` and
  :data:`~app.services.skill_graph.MAX_NODES`.
* ``GET /skills/packs/{pack_id}/lineage`` — one-step neighbourhood,
  cheaper for the side-panel in the SkillPack detail view.

Both routes are workspace-member-only. Cross-workspace pack lookups
return 404 with the same ``skill_pack.not_found`` code used by the
neighbouring routes in ``skills_persistence.py`` so the frontend can
share its error mapping table.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.repositories.skills import SkillPackRepository
from app.schemas.skill_graph import (
    SkillGraphEdge,
    SkillGraphNode,
    SkillGraphRead,
    SkillLineageEdgeRead,
    SkillLineageRead,
)
from app.services import audit as audit_svc
from app.services import skill_graph as graph_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/skills/packs", tags=["skills"])


_GRAPH_READ = Depends(rate_limit("skill_graph_read", limit=30, period_seconds=60))
_LINEAGE_READ = Depends(rate_limit("skill_lineage_read", limit=30, period_seconds=60))


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _ensure_pack(db, *, workspace_id: uuid.UUID, pack_id: uuid.UUID) -> None:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")


@router.get(
    "/{pack_id}/graph",
    response_model=SkillGraphRead,
    dependencies=[_GRAPH_READ],
)
async def get_skill_graph(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    depth: int = Query(2, ge=1, le=graph_svc.MAX_DEPTH),
) -> SkillGraphRead:
    """Return the BFS lineage graph rooted at ``pack_id``.

    ``depth`` is bounded server-side; values larger than
    :data:`~app.services.skill_graph.MAX_DEPTH` are rejected at the
    pydantic layer rather than silently clamped, so a buggy frontend
    surfaces a 422 instead of getting an unexpectedly small graph.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack(db, workspace_id=ws_id, pack_id=pack_id)

    graph = await graph_svc.build_skill_graph(
        db,
        workspace_id=ws_id,
        focus_pack_id=pack_id,
        depth=depth,
    )

    # Audit only when the caller asked for the deepest tier — that's
    # the path admins would invoke during a forensics review and the
    # one we want indexed in the audit feed for retro investigations.
    if graph.depth >= graph_svc.MAX_DEPTH:
        await audit_svc.record(
            db,
            action="skill_graph.queried",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="skill_pack",
            resource_id=pack_id,
            summary=f"deep skill graph queried (depth={graph.depth})",
            metadata={
                "focus_pack_id": str(pack_id),
                "depth": graph.depth,
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "truncated": graph.truncated,
            },
            request=request,
        )
        await db.commit()

    return SkillGraphRead(
        focus_pack_id=graph.focus_pack_id,
        depth=graph.depth,
        truncated=graph.truncated,
        nodes=[
            SkillGraphNode(
                node_id=n.node_id,
                pack_id=n.pack_id,
                slug=n.slug,
                name=n.name,
                state=n.state,
                pinned=n.pinned,
                enabled=n.enabled,
                is_external=n.is_external,
                is_focus=n.is_focus,
            )
            for n in graph.nodes
        ],
        edges=[
            SkillGraphEdge(
                parent_id=e.parent_id,
                child_id=e.child_id,
                kind=e.kind,
                derived_from_run_ids=list(e.derived_from_run_ids),
                metadata=dict(e.metadata),
                created_at=e.created_at,
            )
            for e in graph.edges
        ],
    )


@router.get(
    "/{pack_id}/lineage",
    response_model=SkillLineageRead,
    dependencies=[_LINEAGE_READ],
)
async def get_skill_lineage(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillLineageRead:
    """One-step incoming + outgoing edges for ``pack_id``."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack(db, workspace_id=ws_id, pack_id=pack_id)

    incoming, outgoing = await graph_svc.list_one_step_lineage(
        db, workspace_id=ws_id, pack_id=pack_id
    )
    return SkillLineageRead(
        focus_pack_id=pack_id,
        incoming=[SkillLineageEdgeRead.model_validate(e) for e in incoming],
        outgoing=[SkillLineageEdgeRead.model_validate(e) for e in outgoing],
    )
