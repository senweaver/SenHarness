"""Repository for :class:`~app.db.models.skill_lineage_edge.SkillLineageEdge`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.skill_lineage_edge import SkillLineageEdge, SkillLineageEdgeKind
from app.db.repository import AsyncRepository


class SkillLineageEdgeRepository(AsyncRepository[SkillLineageEdge]):
    model = SkillLineageEdge

    async def list_outgoing(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
    ) -> Sequence[SkillLineageEdge]:
        """Edges where ``parent_pack_id == pack_id`` (descendants)."""
        stmt = select(SkillLineageEdge).where(
            SkillLineageEdge.workspace_id == workspace_id,
            SkillLineageEdge.parent_pack_id == pack_id,
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_incoming(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
    ) -> Sequence[SkillLineageEdge]:
        """Edges where ``child_pack_id == pack_id`` (ancestors)."""
        stmt = select(SkillLineageEdge).where(
            SkillLineageEdge.workspace_id == workspace_id,
            SkillLineageEdge.child_pack_id == pack_id,
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_pack(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
    ) -> Sequence[SkillLineageEdge]:
        """One-step neighbourhood: every edge that touches ``pack_id``
        on either end. Used by the lightweight ``/lineage`` route.
        """
        stmt = select(SkillLineageEdge).where(
            SkillLineageEdge.workspace_id == workspace_id,
            or_(
                SkillLineageEdge.parent_pack_id == pack_id,
                SkillLineageEdge.child_pack_id == pack_id,
            ),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_packs(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_ids: Sequence[uuid.UUID],
    ) -> Sequence[SkillLineageEdge]:
        """Every edge that touches any pack in ``pack_ids``. Used by
        the BFS expansion in :func:`app.services.skill_graph.build_skill_graph`.
        """
        if not pack_ids:
            return []
        ids = list(pack_ids)
        stmt = select(SkillLineageEdge).where(
            SkillLineageEdge.workspace_id == workspace_id,
            or_(
                SkillLineageEdge.parent_pack_id.in_(ids),
                SkillLineageEdge.child_pack_id.in_(ids),
            ),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def upsert_edge(
        self,
        *,
        workspace_id: uuid.UUID,
        parent_pack_id: uuid.UUID | None,
        child_pack_id: uuid.UUID,
        edge_kind: SkillLineageEdgeKind,
        derived_from_run_ids: list[str] | None = None,
        hub_pack_slug: str | None = None,
        metadata_json: dict | None = None,
    ) -> SkillLineageEdge:
        """Insert or no-op-merge an edge.

        The unique constraint
        ``(parent_pack_id, child_pack_id, edge_kind)`` makes a re-insert
        with the same triple a no-op; the existing row is returned with
        its provenance metadata preserved. Run ids supplied on a repeat
        call are merged into the existing array (set semantics) so that
        the same skill version can record multiple supporting runs over
        time.

        ``parent_pack_id=None`` is reserved for ``PULLED_FROM_HUB``
        edges; ``hub_pack_slug`` carries the source slug for the
        external placeholder node.
        """
        run_ids = list(derived_from_run_ids or [])
        meta = dict(metadata_json or {})

        existing = await self._find_existing(
            workspace_id=workspace_id,
            parent_pack_id=parent_pack_id,
            child_pack_id=child_pack_id,
            edge_kind=edge_kind,
        )
        if existing is not None:
            merged = sorted({*(existing.derived_from_run_ids or []), *run_ids})
            if merged != list(existing.derived_from_run_ids or []):
                existing.derived_from_run_ids = merged
            if hub_pack_slug and existing.hub_pack_slug != hub_pack_slug:
                existing.hub_pack_slug = hub_pack_slug
            if meta:
                merged_meta = dict(existing.metadata_json or {})
                merged_meta.update(meta)
                existing.metadata_json = merged_meta
            await self.session.flush([existing])
            return existing

        return await self.create(
            workspace_id=workspace_id,
            parent_pack_id=parent_pack_id,
            child_pack_id=child_pack_id,
            edge_kind=edge_kind,
            derived_from_run_ids=run_ids,
            hub_pack_slug=hub_pack_slug,
            metadata_json=meta,
        )

    async def _find_existing(
        self,
        *,
        workspace_id: uuid.UUID,
        parent_pack_id: uuid.UUID | None,
        child_pack_id: uuid.UUID,
        edge_kind: SkillLineageEdgeKind,
    ) -> SkillLineageEdge | None:
        stmt = select(SkillLineageEdge).where(
            SkillLineageEdge.workspace_id == workspace_id,
            SkillLineageEdge.child_pack_id == child_pack_id,
            SkillLineageEdge.edge_kind == edge_kind,
        )
        if parent_pack_id is None:
            stmt = stmt.where(SkillLineageEdge.parent_pack_id.is_(None))
        else:
            stmt = stmt.where(SkillLineageEdge.parent_pack_id == parent_pack_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


# Re-export ``pg_insert`` symbol so callers that want a true Postgres
# ``ON CONFLICT DO NOTHING`` upsert can do that without re-importing
# the dialect themselves. The repo's :meth:`upsert_edge` already covers
# the common path; this is the escape hatch for migration backfills.
__all__ = ["SkillLineageEdgeRepository", "pg_insert"]
