"""Unit: ``skill_version.create_version`` happy + dedup (M1.2)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models.skill_pack_version import SkillPackVersionState
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import skill_version as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id):
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
        created_by=None,
    )
    await db.flush()
    return pack


async def test_create_version_first_proposal_is_v1_proposed(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    version = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="# v1 body",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    assert version.version_no == 1
    assert version.state == SkillPackVersionState.PROPOSED
    assert version.content_hash
    assert version.created_by == "user"
    assert version.creator_identity_id == identity.id
    assert version.activated_at is None
    assert version.retired_at is None


async def test_second_distinct_content_increments_version_no(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v1 = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="v1",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    v2 = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="v2 different",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    assert v1.version_no == 1
    assert v2.version_no == 2
    assert v1.content_hash != v2.content_hash


async def test_duplicate_content_hash_raises_conflict(db_session, workspace, identity) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    body = "exactly the same body"
    await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=body,
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    with pytest.raises(svc.SkillPackVersionConflict) as exc:
        await svc.create_version(
            db_session,
            workspace_id=workspace.id,
            pack_id=pack.id,
            content_md=body,
            files=None,
            created_by="user",
            creator_identity_id=identity.id,
        )
    assert exc.value.code == "skill_version.duplicate_content_hash"
    assert "existing_version_no" in exc.value.extras


async def test_files_map_changes_hash(db_session, workspace, identity) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v_no_files = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="body",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    v_with_files = await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="body",
        files={"scripts/a.py": "abc123"},
        created_by="user",
        creator_identity_id=identity.id,
    )
    assert v_no_files.content_hash != v_with_files.content_hash


async def test_compute_content_hash_is_deterministic_and_order_independent() -> None:
    a = svc.compute_content_hash("body", {"a": "1", "b": "2"})
    b = svc.compute_content_hash("body", {"b": "2", "a": "1"})
    assert a == b
    assert a != svc.compute_content_hash("body", {"a": "1"})


async def test_repo_next_version_no_starts_at_one(db_session, workspace, identity) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    repo = SkillPackVersionRepository(db_session)
    assert await repo.next_version_no(workspace_id=workspace.id, pack_id=pack.id) == 1
    await svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="body",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    assert await repo.next_version_no(workspace_id=workspace.id, pack_id=pack.id) == 2
