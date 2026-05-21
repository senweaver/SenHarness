"""Unit: ``skill_version`` state machine + audit (M1.2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.skill_pack_version import SkillPackVersionState
from app.repositories.skills import SkillPackRepository
from app.services import skill_version as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="t",
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


async def _new_version(db, *, workspace_id, pack_id, identity, body=""):
    return await svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack_id,
        content_md=body or f"body-{uuid.uuid4().hex[:8]}",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )


async def test_proposed_to_validating_to_accepted_to_active_chain(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v = await _new_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        identity=identity,
    )
    after_validating = await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v.id,
        target_state=SkillPackVersionState.VALIDATING,
        actor_identity_id=identity.id,
        reason="checks running",
    )
    assert after_validating.state == SkillPackVersionState.VALIDATING

    after_accepted = await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v.id,
        target_state=SkillPackVersionState.ACCEPTED,
        actor_identity_id=identity.id,
        reason="checks passed",
    )
    assert after_accepted.state == SkillPackVersionState.ACCEPTED

    after_active = await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v.id,
        target_state=SkillPackVersionState.ACTIVE,
        actor_identity_id=identity.id,
        reason="promote to live",
    )
    assert after_active.state == SkillPackVersionState.ACTIVE


async def test_proposed_to_rejected_terminal(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v = await _new_version(
        db_session, workspace_id=workspace.id, pack_id=pack.id, identity=identity
    )
    rejected = await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v.id,
        target_state=SkillPackVersionState.REJECTED,
        actor_identity_id=identity.id,
        reason="lint failed",
    )
    assert rejected.state == SkillPackVersionState.REJECTED
    with pytest.raises(svc.SkillPackVersionTransitionError):
        await svc.transition_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v.id,
            target_state=SkillPackVersionState.ACTIVE,
            actor_identity_id=identity.id,
            reason="resurrect",
        )


async def test_active_to_retired_after_supersede(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v1 = await _new_version(
        db_session, workspace_id=workspace.id, pack_id=pack.id, identity=identity
    )
    await svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v1.id,
        actor_identity_id=identity.id,
    )
    retired = await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v1.id,
        target_state=SkillPackVersionState.RETIRED,
        actor_identity_id=identity.id,
        reason="manual retire",
    )
    assert retired.state == SkillPackVersionState.RETIRED
    assert retired.retired_at is not None


async def test_invalid_edge_raises(db_session, workspace, identity) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v = await _new_version(
        db_session, workspace_id=workspace.id, pack_id=pack.id, identity=identity
    )
    with pytest.raises(svc.SkillPackVersionTransitionError) as exc:
        await svc.transition_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v.id,
            target_state=SkillPackVersionState.ACTIVE,
            actor_identity_id=identity.id,
            reason="skip validation",
        )
    assert exc.value.code == "skill_version.invalid_transition"
    assert "validating" in exc.value.extras["allowed"]


async def test_transition_writes_audit_row(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v = await _new_version(
        db_session, workspace_id=workspace.id, pack_id=pack.id, identity=identity
    )
    await svc.transition_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v.id,
        target_state=SkillPackVersionState.VALIDATING,
        actor_identity_id=identity.id,
        reason="audit smoke",
    )
    row = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill_version.transitioned",
                    AuditEvent.resource_id == v.id,
                )
                .order_by(AuditEvent.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    meta = row.metadata_json
    assert meta["from"] == "proposed"
    assert meta["to"] == "validating"
    assert meta["reason"] == "audit smoke"


async def test_create_version_writes_audit_row(
    db_session, workspace, identity
) -> None:
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v = await _new_version(
        db_session, workspace_id=workspace.id, pack_id=pack.id, identity=identity
    )
    row = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill_version.created",
                    AuditEvent.resource_id == v.id,
                )
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.metadata_json["version_no"] == 1
    assert row.metadata_json["created_by"] == "user"
