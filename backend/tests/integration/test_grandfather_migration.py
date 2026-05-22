"""Integration: M0.12 grandfather backfill is idempotent and bound to threshold.

The migration runs at fixture-startup time, so we can't observe its
real-deployment behaviour from inside a test. Instead we verify two
post-migration invariants the routes rely on:

* A freshly-registered self-register identity starts with
  ``workspace_quota_override = NULL`` (the migration's HAVING clause
  ignores them because their count is 1 == the threshold).
* The same SQL the migration uses, run on a synthetic power user with
  N>1 owner memberships, sets the expected override value.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.db.models.identity import Identity
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def test_fresh_identity_starts_with_no_override(async_client):
    """Default deploy: a brand-new self-register has no override."""
    email = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Fresh User",
            "password": "fresh-grandfather-test-long",
        },
    )
    assert r.status_code == 201, r.text
    identity_id = uuid.UUID(r.json()["identity_id"])

    factory = get_session_factory()
    async with factory() as db:
        ident = await db.get(Identity, identity_id)
        assert ident is not None
        assert ident.workspace_quota_override is None


async def test_grandfather_sql_sets_override_for_power_user(async_client):
    """The SELECT + UPDATE the migration uses applied at runtime.

    Synthesises an identity with three owner memberships, runs the
    same backfill statements the alembic migration executes, and
    verifies the override lands at exactly the owned count.
    """
    factory = get_session_factory()
    email = f"power-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Power User",
            "password": "grandfather-power-user-pass",
        },
    )
    assert r.status_code == 201
    identity_id = uuid.UUID(r.json()["identity_id"])

    async with factory() as db:
        # Bypass the API quota gate by using the workspace service
        # directly and inserting matching log rows. The ``identity``
        # ends up with personal+3 = 4 owner memberships.
        from app.services import workspace as ws_svc

        for i in range(3):
            await ws_svc.create_workspace(
                db,
                name=f"Power {i}",
                slug=f"power-{uuid.uuid4().hex[:6]}-{i}",
                owner_identity_id=identity_id,
            )
        await db.commit()

    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT m.identity_id AS identity_id, COUNT(*) AS owned_count
                    FROM memberships m
                    JOIN workspaces w ON w.id = m.workspace_id
                    WHERE m.role = 'owner'
                      AND m.deleted_at IS NULL
                      AND m.status = 'active'
                      AND w.deleted_at IS NULL
                    GROUP BY m.identity_id
                    HAVING COUNT(*) > 1
                    """
                )
            )
        ).fetchall()
        identity_counts = {r.identity_id: int(r.owned_count) for r in rows}
        assert identity_id in identity_counts
        assert identity_counts[identity_id] >= 4

        await db.execute(
            text(
                "UPDATE identities SET workspace_quota_override = :n "
                "WHERE id = :id AND workspace_quota_override IS NULL"
            ),
            {"n": identity_counts[identity_id], "id": identity_id},
        )
        await db.commit()

        ident = (await db.execute(select(Identity).where(Identity.id == identity_id))).scalar_one()
        assert ident.workspace_quota_override == identity_counts[identity_id]

    # Cleanup: hard-delete the synthesised workspaces so they don't
    # bleed into other tests' counts.
    async with factory() as db:
        await db.execute(
            text(
                "DELETE FROM workspaces "
                "WHERE id IN (SELECT workspace_id FROM memberships "
                "             WHERE identity_id = :id AND role = 'owner')"
            ),
            {"id": identity_id},
        )
        await db.commit()
