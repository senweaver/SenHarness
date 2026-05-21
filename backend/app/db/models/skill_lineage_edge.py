"""SkillPack lineage edges (M4.2).

Records the *truth* relationships between SkillPacks rather than
similarity. Four edge kinds cover every provenance link the runtime
emits:

* ``derived_from`` — a candidate SkillPackVersion was proposed by the
  evolver from one or more supporting run ids; the edge anchors on
  the parent SkillPack the proposal patches/edits (``patch`` /
  ``edit`` verbs). The supporting run ids land on
  ``derived_from_run_ids`` so the M4.3 lineage replay job can walk
  back to the original sessions without joining
  :class:`~app.db.models.skill_pack_version.SkillPackVersion`.
* ``supersedes`` — a SkillPack replaces an older one; the M1.2
  ``superseded_by_pack_id`` column on :class:`SkillPack` is the
  source of truth and the edge is the indexed counterpart for
  graph traversal in either direction.
* ``forked_from`` — placeholder for an admin-initiated workspace
  fork (M4.x, not implemented in M4.2). The edge schema accepts
  the kind today so existing migrations don't have to change when
  the fork verb lands.
* ``pulled_from_hub`` — workspace SkillPack was pulled from a
  :class:`~app.db.models.hub_skill_pack.HubSkillPack`. ``parent_pack_id``
  is NULL because the parent lives on the hub (cross-workspace);
  ``hub_pack_slug`` carries the slug so the graph view can render
  a sanitized external node without exposing source-workspace
  metadata.

Cross-workspace isolation
-------------------------

Every edge is workspace-scoped via :class:`WorkspaceScopedMixin`.
Hub-pull edges deliberately don't store a foreign-key to the source
workspace pack — the M4.2 graph builder synthesises an external
placeholder node from ``hub_pack_slug`` so the workspace never sees
metadata that belongs to another tenant.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SkillLineageEdgeKind(StrEnum):
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"
    FORKED_FROM = "forked_from"
    PULLED_FROM_HUB = "pulled_from_hub"


SKILL_LINEAGE_EDGE_KIND_VALUES: tuple[str, ...] = tuple(
    k.value for k in SkillLineageEdgeKind
)


class SkillLineageEdge(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "skill_lineage_edges"

    parent_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    child_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edge_kind: Mapped[SkillLineageEdgeKind] = mapped_column(
        SAEnum(
            SkillLineageEdgeKind,
            native_enum=False,
            length=32,
            name="skill_lineage_edge_kind",
        ),
        nullable=False,
        index=True,
    )
    derived_from_run_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
        nullable=False,
    )
    hub_pack_slug: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "parent_pack_id",
            "child_pack_id",
            "edge_kind",
            name="uq_skill_lineage_edges_parent_child_kind",
        ),
        Index(
            "ix_skill_lineage_edges_workspace_child",
            "workspace_id",
            "child_pack_id",
        ),
        Index(
            "ix_skill_lineage_edges_workspace_parent",
            "workspace_id",
            "parent_pack_id",
        ),
    )
