"""Unit: slug tombstone semantics for personal workspace + manual create.

Asserts:

* :func:`workspace_quota.is_slug_tombstoned` flips to True after a
  ``DELETE /workspaces/{id}`` raw-SQL update sets ``slug_tombstoned``.
* The personal-workspace allocator skips a tombstoned base candidate
  and falls through to the linear suffix.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.db.models.workspace_creation_log import (
    CreationKind,
    WorkspaceCreationLog,
)
from app.services import workspace as workspace_svc
from app.services import workspace_quota as quota_svc
from app.services.personal_workspace import reserve_personal_workspace_slug

pytestmark = pytest.mark.asyncio


async def test_is_slug_tombstoned_flips_after_delete(db_session, identity):
    slug = f"ghost-{uuid.uuid4().hex[:6]}"
    ws = await workspace_svc.create_workspace(
        db_session,
        name="Ghost",
        slug=slug,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    assert await quota_svc.is_slug_tombstoned(db_session, slug=slug) is False

    await db_session.execute(
        text("UPDATE workspaces SET deleted_at = now(), slug_tombstoned = TRUE WHERE id = :id"),
        {"id": ws.id},
    )
    await db_session.flush()

    assert await quota_svc.is_slug_tombstoned(db_session, slug=slug) is True


async def test_personal_allocator_skips_tombstoned_base(db_session, identity):
    """Tombstoned slug forces the allocator into the ``-2`` fallback."""
    base_seed = f"tomb{uuid.uuid4().hex[:6]}"
    ws = await workspace_svc.create_workspace(
        db_session,
        name="Existing",
        slug=base_seed,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    # Tombstone it.
    await db_session.execute(
        text("UPDATE workspaces SET deleted_at = now(), slug_tombstoned = TRUE WHERE id = :id"),
        {"id": ws.id},
    )
    await db_session.flush()

    slug, _used_random = await reserve_personal_workspace_slug(
        db_session, email=f"{base_seed}@example.com"
    )
    # Tombstoned base must not be reused; allocator returns the next
    # linear candidate (or random tail if it also collides).
    assert slug != base_seed


async def test_release_on_delete_marks_log_rows(db_session, identity):
    ws = await workspace_svc.create_workspace(
        db_session,
        name="Logged",
        slug=f"logged-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await quota_svc.record_creation(
        db_session,
        identity_id=identity.id,
        workspace_id=ws.id,
        creation_kind=CreationKind.MANUAL,
    )
    await db_session.flush()

    rows_before = (
        (
            await db_session.execute(
                select(WorkspaceCreationLog).where(WorkspaceCreationLog.workspace_id == ws.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_before) == 1
    assert rows_before[0].soft_deleted_workspace is False

    affected = await quota_svc.release_on_delete(
        db_session, workspace_id=ws.id, actor_identity_id=identity.id
    )
    await db_session.flush()
    assert affected == 1

    rows_after = (
        (
            await db_session.execute(
                select(WorkspaceCreationLog).where(WorkspaceCreationLog.workspace_id == ws.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows_after[0].soft_deleted_workspace is True
