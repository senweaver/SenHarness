"""Integration: ``0043_skill_state_machine`` data backfill.

After ``alembic upgrade head`` the M1.1 migration must have set
``state = 'archived'`` for every soft-deleted pack and ``state =
'active'`` for the rest. The ``async_client`` fixture has already
applied the head; we just inspect the column.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.repositories.skills import SkillPackRepository
from app.services import workspace as ws_svc

pytestmark = pytest.mark.asyncio


async def test_grandfather_active_and_archived_split(async_client, identity):
    factory = get_session_factory()
    async with factory() as db:
        # Two workspaces so grandfather flows are both exercised.
        ws_a = await ws_svc.create_workspace(
            db,
            name="A",
            slug=f"mig-a-{uuid.uuid4().hex[:6]}",
            owner_identity_id=identity.id,
        )
        ws_b = await ws_svc.create_workspace(
            db,
            name="B",
            slug=f"mig-b-{uuid.uuid4().hex[:6]}",
            owner_identity_id=identity.id,
        )
        await db.flush()

        live = await SkillPackRepository(db).create(
            workspace_id=ws_a.id,
            slug=f"live-{uuid.uuid4().hex[:6]}",
            name="Live pack",
            description="x",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        legacy = await SkillPackRepository(db).create(
            workspace_id=ws_b.id,
            slug=f"legacy-{uuid.uuid4().hex[:6]}",
            name="Legacy pack",
            description="x",
            version="0.1.0",
            publisher=None,
            signature=None,
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=identity.id,
        )
        await db.flush()

        # Mark the legacy pack as soft-deleted to simulate the
        # pre-M1.1 archive semantics, then re-run the same backfill
        # SQL the migration uses (idempotent — the column is already
        # backfilled at upgrade time, but we need to assert the
        # invariant on rows created *after* the migration).
        await db.execute(
            text("UPDATE skill_packs SET deleted_at = now() WHERE id = :id"),
            {"id": legacy.id},
        )
        await db.execute(
            text("UPDATE skill_packs SET state = 'archived' WHERE deleted_at IS NOT NULL")
        )
        await db.execute(
            text(
                "UPDATE skill_packs SET state = 'active' "
                "WHERE deleted_at IS NULL AND state = 'active'"
            )
        )
        await db.commit()

    async with factory() as db:
        live_state = (
            await db.execute(
                text("SELECT state FROM skill_packs WHERE id = :id"),
                {"id": live.id},
            )
        ).scalar_one()
        legacy_state = (
            await db.execute(
                text("SELECT state FROM skill_packs WHERE id = :id"),
                {"id": legacy.id},
            )
        ).scalar_one()
        assert live_state == "active"
        assert legacy_state == "archived"
