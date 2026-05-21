"""DTOs for the M4.2 skill knowledge graph endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.skill_lineage_edge import SkillLineageEdgeKind
from app.schemas._base import ORMModel


class SkillLineageEdgeRead(ORMModel):
    """One persisted lineage edge.

    The wire shape uses ``parent_pack_id`` and ``child_pack_id`` as
    optional UUIDs; ``parent_pack_id`` is null for ``pulled_from_hub``
    edges (the parent lives on the hub, not in the workspace).
    ``hub_pack_slug`` is set on hub-pull edges and used by the
    frontend to render the external placeholder node.
    """

    id: uuid.UUID
    parent_pack_id: uuid.UUID | None = None
    child_pack_id: uuid.UUID
    edge_kind: SkillLineageEdgeKind
    derived_from_run_ids: list[str] = Field(default_factory=list)
    hub_pack_slug: str | None = None
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SkillGraphNode(ORMModel):
    """One node in the response graph.

    ``pack_id`` is null for synthesized hub-source nodes (``is_external``).
    The frontend keys nodes by :attr:`node_id`; that is the same string
    used by :attr:`SkillGraphEdge.parent_id` / ``child_id`` so a
    react-flow consumer can wire edges without a separate lookup.
    """

    node_id: str
    pack_id: uuid.UUID | None = None
    slug: str
    name: str
    state: str | None = None
    pinned: bool = False
    enabled: bool = True
    is_external: bool = False
    is_focus: bool = False


class SkillGraphEdge(ORMModel):
    parent_id: str
    child_id: str
    kind: SkillLineageEdgeKind
    derived_from_run_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class SkillGraphRead(ORMModel):
    """The /graph response: a focus pack plus a BFS-bounded neighbourhood."""

    focus_pack_id: uuid.UUID
    depth: int
    nodes: list[SkillGraphNode]
    edges: list[SkillGraphEdge]
    truncated: bool = False
    """True when the neighbourhood was capped by ``MAX_NODES`` and the
    BFS stopped expanding before exhausting the requested depth.
    """


class SkillLineageRead(ORMModel):
    """The lightweight /lineage response: 1-step neighbourhood only.

    Used by the side-panel in the M4.2 SkillPack detail view; the heavy
    /graph route is reserved for the dedicated graph page.
    """

    focus_pack_id: uuid.UUID
    incoming: list[SkillLineageEdgeRead]
    outgoing: list[SkillLineageEdgeRead]
