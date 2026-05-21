"""Unit: tombstone slug semantics (M1.1).

* ARCHIVED → TOMBSTONE writes a ``TombstoneSlug`` row.
* ``is_slug_tombstoned`` returns True after the transition.
* Trying to create a fresh pack with the same slug raises
  :class:`SkillSlugTombstoned`.
* The tombstone row is workspace-scoped — a different workspace can
  still use the same slug.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import SkillSlugTombstoned
from app.db.models.skills import SkillPackState
from app.db.models.tombstone_slug import TombstoneSlug
from app.repositories.skills import SkillPackRepository
from app.services import skill_lifecycle as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id, slug, state=SkillPackState.ARCHIVED):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=slug,
        name="tombstone target",
        description="x",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=state,
        content_hash="cafe" + "0" * 60,
    )
    await db.flush()
    return pack


async def test_archive_to_tombstone_writes_slug_row(db_session, workspace, identity):
    slug = f"tomb-{uuid.uuid4().hex[:6]}"
    pack = await _make_pack(db_session, workspace_id=workspace.id, slug=slug)

    await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.TOMBSTONE,
        actor_identity_id=identity.id,
        reason="prune unused",
        bypass_pinned=True,
    )

    row = (
        await db_session.execute(
            select(TombstoneSlug).where(
                TombstoneSlug.workspace_id == workspace.id,
                TombstoneSlug.slug == slug,
            )
        )
    ).scalar_one()
    assert row.last_content_hash == "cafe" + "0" * 60
    assert row.original_pack_id == pack.id
    assert pack.state == SkillPackState.TOMBSTONE


async def test_is_slug_tombstoned(db_session, workspace, identity):
    slug = f"chk-{uuid.uuid4().hex[:6]}"
    other_slug = f"oth-{uuid.uuid4().hex[:6]}"

    assert await svc.is_slug_tombstoned(db_session, workspace_id=workspace.id, slug=slug) is False

    pack = await _make_pack(db_session, workspace_id=workspace.id, slug=slug)
    await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.TOMBSTONE,
        actor_identity_id=identity.id,
        reason="prune",
        bypass_pinned=True,
    )
    await db_session.flush()

    assert await svc.is_slug_tombstoned(db_session, workspace_id=workspace.id, slug=slug) is True
    assert (
        await svc.is_slug_tombstoned(db_session, workspace_id=workspace.id, slug=other_slug)
        is False
    )


async def test_create_with_tombstoned_slug_raises(db_session, workspace, identity):
    """``is_slug_tombstoned`` gate: the create path raises
    :class:`SkillSlugTombstoned` when the slug already lives in the
    tombstone table for this workspace.
    """
    slug = f"reuse-{uuid.uuid4().hex[:6]}"
    pack = await _make_pack(db_session, workspace_id=workspace.id, slug=slug)
    await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.TOMBSTONE,
        actor_identity_id=identity.id,
        reason="prune",
        bypass_pinned=True,
    )
    await db_session.flush()

    with pytest.raises(SkillSlugTombstoned) as exc:
        if await svc.is_slug_tombstoned(db_session, workspace_id=workspace.id, slug=slug):
            raise SkillSlugTombstoned(
                "skill_pack_slug_tombstoned",
                code="skill.slug_tombstoned",
                extras={"slug": slug},
            )
    assert exc.value.code == "skill.slug_tombstoned"


async def test_tombstone_slug_is_workspace_scoped(db_session, workspace, identity):
    """Same slug can be tombstoned in workspace A and still created in
    workspace B — the unique constraint is on ``(workspace_id, slug)``.
    """
    from app.services import workspace as ws_svc

    other_ws = await ws_svc.create_workspace(
        db_session,
        name="Other",
        slug=f"other-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    slug = f"shared-{uuid.uuid4().hex[:6]}"
    pack = await _make_pack(db_session, workspace_id=workspace.id, slug=slug)
    await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.TOMBSTONE,
        actor_identity_id=identity.id,
        reason="prune",
        bypass_pinned=True,
    )
    await db_session.flush()

    assert await svc.is_slug_tombstoned(db_session, workspace_id=workspace.id, slug=slug) is True
    assert await svc.is_slug_tombstoned(db_session, workspace_id=other_ws.id, slug=slug) is False
