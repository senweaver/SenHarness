"""Unit: ``skill_version.rollback_to_version`` (M1.2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.skill_pack_version import SkillPackVersionState
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import skill_version as svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(db, *, workspace_id, identity):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="rollback target",
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
        content_md="v1 body",
    )
    await db.flush()

    v1 = await svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        content_md="v1 body",
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
    v2 = await svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        content_md="v2 body — newer but worse",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    await svc.activate_version(
        db,
        workspace_id=workspace_id,
        version_id=v2.id,
        actor_identity_id=identity.id,
    )
    return pack, v1, v2


async def test_rollback_promotes_old_version_back_to_active(
    db_session, workspace, identity
) -> None:
    pack, v1, v2 = await _bootstrap(
        db_session, workspace_id=workspace.id, identity=identity
    )
    rolled = await svc.rollback_to_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_version_id=v1.id,
        actor_identity_id=identity.id,
        reason="v2 broke prod",
    )
    await db_session.refresh(v2)
    await db_session.refresh(pack)
    assert rolled.id == v1.id
    assert rolled.state == SkillPackVersionState.ACTIVE
    assert v2.state == SkillPackVersionState.RETIRED
    assert pack.content_hash == v1.content_hash


async def test_rollback_writes_dedicated_audit_row(
    db_session, workspace, identity
) -> None:
    pack, v1, _v2 = await _bootstrap(
        db_session, workspace_id=workspace.id, identity=identity
    )
    await svc.rollback_to_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_version_id=v1.id,
        actor_identity_id=identity.id,
        reason="rollback audit smoke",
    )
    row = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill_version.rollback",
                    AuditEvent.resource_id == v1.id,
                )
                .order_by(AuditEvent.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.metadata_json["target_version_no"] == 1
    assert row.metadata_json["reason"] == "rollback audit smoke"


async def test_rollback_to_unknown_version_raises_not_found(
    db_session, workspace, identity
) -> None:
    pack, _v1, _v2 = await _bootstrap(
        db_session, workspace_id=workspace.id, identity=identity
    )
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        await svc.rollback_to_version(
            db_session,
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_version_id=uuid.uuid4(),
            actor_identity_id=identity.id,
            reason="missing target",
        )


async def test_rollback_target_must_belong_to_pack(
    db_session, workspace, identity
) -> None:
    pack_a, v1_a, _ = await _bootstrap(
        db_session, workspace_id=workspace.id, identity=identity
    )
    pack_b, _v1_b, _v2_b = await _bootstrap(
        db_session, workspace_id=workspace.id, identity=identity
    )
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        await svc.rollback_to_version(
            db_session,
            workspace_id=workspace.id,
            pack_id=pack_b.id,
            target_version_id=v1_a.id,
            actor_identity_id=identity.id,
            reason="cross-pack rollback",
        )
