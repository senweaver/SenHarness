"""Unit: ``skill_version.activate_version`` retire-previous + cache sync (M1.2)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models.skill_pack_version import SkillPackVersionState
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import skill_version as svc

pytestmark = pytest.mark.asyncio


async def _make_pack_with_v1(db, *, workspace_id, identity):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Test pack",
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
        workspace_id=workspace_id,
        skill_pack_id=pack.id,
        path="SKILL.md",
        content_md="initial body",
    )
    await db.flush()
    v1 = await svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        content_md="initial body",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    await svc.activate_version(
        db,
        workspace_id=workspace_id,
        version_id=v1.id,
        actor_identity_id=identity.id,
    )
    return pack, v1


async def test_activate_first_version_sets_active_and_mirrors(
    db_session, workspace, identity
) -> None:
    pack, v1 = await _make_pack_with_v1(
        db_session, workspace_id=workspace.id, identity=identity
    )
    await db_session.refresh(v1)
    await db_session.refresh(pack)
    assert v1.state == SkillPackVersionState.ACTIVE
    assert v1.activated_at is not None
    assert pack.content_hash == v1.content_hash


async def test_activate_new_version_retires_previous(
    db_session, workspace, identity
) -> None:
    pack, v1 = await _make_pack_with_v1(
        db_session, workspace_id=workspace.id, identity=identity
    )
    v2 = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="rewritten body for v2",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    activated = await svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v2.id,
        actor_identity_id=identity.id,
    )
    await db_session.refresh(v1)
    await db_session.refresh(pack)
    assert activated.state == SkillPackVersionState.ACTIVE
    assert v1.state == SkillPackVersionState.RETIRED
    assert v1.retired_at is not None
    assert v1.superseded_by_version_id == v2.id
    assert pack.content_hash == v2.content_hash


async def test_activate_idempotent_when_already_active(
    db_session, workspace, identity
) -> None:
    pack, v1 = await _make_pack_with_v1(
        db_session, workspace_id=workspace.id, identity=identity
    )
    activated = await svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v1.id,
        actor_identity_id=identity.id,
    )
    assert activated.state == SkillPackVersionState.ACTIVE
    repo = SkillPackVersionRepository(db_session)
    rows = await repo.list_for_pack(
        workspace_id=workspace.id, pack_id=pack.id, limit=10
    )
    active_rows = [r for r in rows if r.state == SkillPackVersionState.ACTIVE]
    assert len(active_rows) == 1


async def test_activate_rejected_version_raises(
    db_session, workspace, identity
) -> None:
    pack, _ = await _make_pack_with_v1(
        db_session, workspace_id=workspace.id, identity=identity
    )
    v2 = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="rejected body",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v2.id,
        target_state=SkillPackVersionState.REJECTED,
        actor_identity_id=identity.id,
        reason="bad draft",
    )
    with pytest.raises(svc.SkillPackVersionTransitionError):
        await svc.activate_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v2.id,
            actor_identity_id=identity.id,
        )


async def test_activate_syncs_skill_file_content(
    db_session, workspace, identity
) -> None:
    pack, _ = await _make_pack_with_v1(
        db_session, workspace_id=workspace.id, identity=identity
    )
    new_body = "new SKILL.md body for v2"
    v2 = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=new_body,
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    await svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v2.id,
        actor_identity_id=identity.id,
    )
    files = await SkillFileRepository(db_session).list_for_pack(
        workspace_id=workspace.id, skill_pack_id=pack.id
    )
    skill_md = next(f for f in files if f.path == "SKILL.md")
    assert skill_md.content_md == new_body
