"""Integration: ``0058_skill_lineage_edges`` backfill (M4.2).

The migration runs at fixture-setup time. Here we re-execute the same
three INSERT statements (idempotent thanks to
``ON CONFLICT DO NOTHING``) against fixture data to assert the
historical-edge backfill matches the spec:

* SkillPack.superseded_by_pack_id non-null → SUPERSEDES edge.
* SkillPackVersion.source_run_ids non-empty + version_no > 1 →
  DERIVED_FROM self-loop edge.
* SkillPack.metadata_json.hub.hub_pack_id present → PULLED_FROM_HUB
  edge with the hub pack's slug.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.models.skill_lineage_edge import SkillLineageEdgeKind
from app.db.session import get_session_factory
from app.repositories.skill_lineage_edge import SkillLineageEdgeRepository
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import workspace as ws_svc

pytestmark = pytest.mark.asyncio


_BACKFILL_SUPERSEDES = """
INSERT INTO skill_lineage_edges (
    workspace_id, parent_pack_id, child_pack_id, edge_kind,
    derived_from_run_ids, metadata_json
)
SELECT
    old.workspace_id,
    old.id,
    old.superseded_by_pack_id,
    'supersedes',
    '[]'::jsonb,
    jsonb_build_object('source', 'migration_0058')
FROM skill_packs AS old
JOIN skill_packs AS newer
  ON newer.id = old.superseded_by_pack_id
WHERE old.superseded_by_pack_id IS NOT NULL
  AND old.workspace_id = newer.workspace_id
ON CONFLICT ON CONSTRAINT uq_skill_lineage_edges_parent_child_kind
DO NOTHING
"""

_BACKFILL_DERIVED = """
INSERT INTO skill_lineage_edges (
    workspace_id, parent_pack_id, child_pack_id, edge_kind,
    derived_from_run_ids, metadata_json
)
SELECT
    v.workspace_id,
    v.pack_id,
    v.pack_id,
    'derived_from',
    v.source_run_ids,
    jsonb_build_object(
        'previous_version_no', v.version_no - 1,
        'created_by', v.created_by,
        'source', 'migration_0058'
    )
FROM skill_pack_versions v
JOIN skill_packs p ON p.id = v.pack_id
WHERE v.version_no > 1
  AND jsonb_typeof(v.source_run_ids) = 'array'
  AND jsonb_array_length(v.source_run_ids) > 0
ON CONFLICT ON CONSTRAINT uq_skill_lineage_edges_parent_child_kind
DO NOTHING
"""

_BACKFILL_HUB = """
INSERT INTO skill_lineage_edges (
    workspace_id, parent_pack_id, child_pack_id, edge_kind,
    derived_from_run_ids, hub_pack_slug, metadata_json
)
SELECT
    p.workspace_id,
    NULL,
    p.id,
    'pulled_from_hub',
    '[]'::jsonb,
    hp.slug,
    jsonb_build_object(
        'hub_pack_id', hp.id::text,
        'source', 'migration_0058'
    )
FROM skill_packs p
JOIN hub_skill_packs hp
  ON hp.id::text = p.metadata_json #>> '{hub,hub_pack_id}'
WHERE p.metadata_json #>> '{hub,hub_pack_id}' IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM skill_lineage_edges existing
      WHERE existing.parent_pack_id IS NULL
        AND existing.child_pack_id = p.id
        AND existing.edge_kind = 'pulled_from_hub'
  )
"""


async def test_backfill_supersedes_and_derived_and_hub(async_client, identity):
    factory = get_session_factory()
    async with factory() as db:
        ws = await ws_svc.create_workspace(
            db,
            name=f"lineage-{uuid.uuid4().hex[:6]}",
            slug=f"lineage-{uuid.uuid4().hex[:6]}",
            owner_identity_id=identity.id,
        )
        pack_repo = SkillPackRepository(db)
        old_pack = await pack_repo.create(
            workspace_id=ws.id,
            slug=f"old-{uuid.uuid4().hex[:6]}",
            name="old",
            description="",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        new_pack = await pack_repo.create(
            workspace_id=ws.id,
            slug=f"new-{uuid.uuid4().hex[:6]}",
            name="new",
            description="",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        old_pack.superseded_by_pack_id = new_pack.id
        await db.flush([old_pack])

        # Insert a hub skill pack manually so the hub-pull backfill has
        # a target row to join against.
        hub_pack_id = uuid.uuid4()
        await db.execute(
            text(
                """
                INSERT INTO hub_skill_packs (
                    id, scope, tenant_id, slug, name, description, state, tags
                ) VALUES (
                    :id, 'tenant', :tid, :slug, 'hub pack', NULL, 'active',
                    '[]'::jsonb
                )
                """
            ),
            {
                "id": hub_pack_id,
                "tid": uuid.uuid4(),
                "slug": f"hub-{uuid.uuid4().hex[:6]}",
            },
        )
        # Slug we actually inserted is unknown to python; read it back.
        hub_slug = (
            await db.execute(
                text("SELECT slug FROM hub_skill_packs WHERE id = :id"),
                {"id": hub_pack_id},
            )
        ).scalar_one()

        pulled_pack = await pack_repo.create(
            workspace_id=ws.id,
            slug=f"pulled-{uuid.uuid4().hex[:6]}",
            name="pulled",
            description="",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=False,
            metadata_json={"hub": {"hub_pack_id": str(hub_pack_id)}},
            created_by=identity.id,
        )

        # Append a v2 SkillPackVersion with source_run_ids set so the
        # derived-from backfill has work to do. The default v1 row is
        # auto-created by the M1.2 grandfather migration; we only need
        # the v2 evolver-style snapshot here.
        run_ids = ["00000000-0000-0000-0000-000000000aaa"]
        v_repo = SkillPackVersionRepository(db)
        await v_repo.create(
            workspace_id=ws.id,
            pack_id=new_pack.id,
            version_no=2,
            content_hash="deadbeef",
            content_md="updated",
            files_json={},
            state="proposed",
            created_by="evolver",
            source_run_ids=run_ids,
        )
        await db.commit()

        # Make sure the workspace starts clean of edges from prior runs.
        await db.execute(
            text("DELETE FROM skill_lineage_edges WHERE workspace_id = :w"),
            {"w": ws.id},
        )
        await db.commit()

        # Replay the backfill SQL in this transaction. The migration
        # has already run as part of fixture setup but this exercises
        # the same query under known seed data.
        await db.execute(text(_BACKFILL_SUPERSEDES))
        await db.execute(text(_BACKFILL_DERIVED))
        await db.execute(text(_BACKFILL_HUB))
        await db.commit()

        edge_repo = SkillLineageEdgeRepository(db)
        all_edges = list(
            await edge_repo.list_for_packs(
                workspace_id=ws.id,
                pack_ids=(old_pack.id, new_pack.id, pulled_pack.id),
            )
        )

    by_kind: dict[SkillLineageEdgeKind, list] = {
        SkillLineageEdgeKind.SUPERSEDES: [],
        SkillLineageEdgeKind.DERIVED_FROM: [],
        SkillLineageEdgeKind.PULLED_FROM_HUB: [],
    }
    for edge in all_edges:
        if edge.edge_kind in by_kind:
            by_kind[edge.edge_kind].append(edge)

    supers = by_kind[SkillLineageEdgeKind.SUPERSEDES]
    assert any(e.parent_pack_id == old_pack.id and e.child_pack_id == new_pack.id for e in supers)

    deriveds = by_kind[SkillLineageEdgeKind.DERIVED_FROM]
    assert any(
        e.parent_pack_id == new_pack.id
        and e.child_pack_id == new_pack.id
        and run_ids[0] in (e.derived_from_run_ids or [])
        for e in deriveds
    )

    hub_edges = by_kind[SkillLineageEdgeKind.PULLED_FROM_HUB]
    assert any(e.child_pack_id == pulled_pack.id and e.hub_pack_slug == hub_slug for e in hub_edges)
