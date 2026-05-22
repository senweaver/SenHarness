"""Integration: 0044 backfill creates v1 ACTIVE per existing pack (M1.2).

The fixture-mounted alembic chain has already run by the time
``async_client`` resolves; we don't re-run it here. Instead we
* create a SkillPack via the raw repository (bypassing API which
  itself now seeds v1) so we get a row matching the "pre-M1.2 grandfather"
  shape,
* run the same backfill SQL the migration uses (idempotent — guarded
  by ``NOT EXISTS``),
* assert each pack ends up with exactly one ``state='active'``
  ``SkillPackVersion`` row whose ``content_md`` equals the SkillFile
  body and whose ``content_hash`` matches what the runtime would
  compute.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import workspace as ws_svc

pytestmark = pytest.mark.asyncio


async def test_backfill_creates_v1_for_each_legacy_pack(async_client, identity):
    factory = get_session_factory()
    body_a = "legacy A body"
    body_b = "legacy B body"
    async with factory() as db:
        ws = await ws_svc.create_workspace(
            db,
            name="Mig",
            slug=f"mig-v-{uuid.uuid4().hex[:6]}",
            owner_identity_id=identity.id,
        )
        await db.flush()
        pack_a = await SkillPackRepository(db).create(
            workspace_id=ws.id,
            slug=f"pa-{uuid.uuid4().hex[:6]}",
            name="A",
            description="x",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        pack_b = await SkillPackRepository(db).create(
            workspace_id=ws.id,
            slug=f"pb-{uuid.uuid4().hex[:6]}",
            name="B",
            description="x",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        await SkillFileRepository(db).create(
            workspace_id=ws.id,
            skill_pack_id=pack_a.id,
            path="SKILL.md",
            content_md=body_a,
        )
        await SkillFileRepository(db).create(
            workspace_id=ws.id,
            skill_pack_id=pack_b.id,
            path="SKILL.md",
            content_md=body_b,
        )
        # Wipe whatever the API auto-created v1 logic might leave (it
        # only fires through the route handler — direct repository
        # writes don't trigger it). So far so good; nothing to wipe.
        await db.commit()

    async with factory() as db:
        # Re-run the same idempotent backfill SQL the migration uses.
        await db.execute(
            text(
                """
                INSERT INTO skill_pack_versions (
                    workspace_id, pack_id, version_no, content_hash,
                    content_md, files_json, state, created_by,
                    creator_identity_id, source_run_ids, validation_results,
                    activated_at, created_at, updated_at
                )
                SELECT
                    p.workspace_id, p.id, 1,
                    encode(digest(COALESCE(sf.content_md, ''), 'sha256'), 'hex'),
                    COALESCE(sf.content_md, ''),
                    '{}'::jsonb, 'active', 'migration', p.created_by,
                    '[]'::jsonb, '{}'::jsonb,
                    p.created_at, p.created_at, p.created_at
                FROM skill_packs p
                LEFT JOIN LATERAL (
                    SELECT content_md FROM skill_files
                    WHERE skill_files.skill_pack_id = p.id
                      AND skill_files.path = 'SKILL.md'
                      AND skill_files.deleted_at IS NULL
                    ORDER BY skill_files.updated_at DESC LIMIT 1
                ) sf ON TRUE
                WHERE NOT EXISTS (
                    SELECT 1 FROM skill_pack_versions v WHERE v.pack_id = p.id
                )
                """
            )
        )
        await db.commit()

    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT pack_id, version_no, state, content_md, content_hash, "
                    "created_by FROM skill_pack_versions "
                    "WHERE pack_id IN (:a, :b) ORDER BY pack_id, version_no"
                ),
                {"a": pack_a.id, "b": pack_b.id},
            )
        ).all()
        by_pack: dict = {}
        for r in rows:
            by_pack.setdefault(r.pack_id, []).append(r)
        assert len(by_pack[pack_a.id]) == 1
        assert len(by_pack[pack_b.id]) == 1
        a_row = by_pack[pack_a.id][0]
        b_row = by_pack[pack_b.id][0]
        assert a_row.version_no == 1 and a_row.state == "active"
        assert b_row.version_no == 1 and b_row.state == "active"
        assert a_row.content_md == body_a
        assert b_row.content_md == body_b
        assert a_row.created_by == "migration"
        assert a_row.content_hash == hashlib.sha256(body_a.encode("utf-8")).hexdigest()
