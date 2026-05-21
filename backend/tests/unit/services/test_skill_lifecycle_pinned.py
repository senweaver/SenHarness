"""Unit: pinned-pack exemption + pin/unpin idempotency (M1.1)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_lifecycle as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id, state=SkillPackState.ACTIVE):
    return await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Pinned test",
        description="x",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=state,
    )


async def test_pin_pack_sets_flag_idempotent(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    await db_session.flush()

    await svc.pin_pack(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
    )
    assert pack.pinned is True
    assert pack.state == SkillPackState.ACTIVE  # state untouched

    await svc.pin_pack(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
    )
    assert pack.pinned is True


async def test_unpin_pack_clears_flag_idempotent(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    pack.pinned = True
    await db_session.flush()

    await svc.unpin_pack(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
    )
    assert pack.pinned is False
    assert pack.state == SkillPackState.ACTIVE


async def test_auto_flow_skipped_when_pinned(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    pack.pinned = True
    await db_session.flush()

    with pytest.raises(svc.PackPinnedAutoSkipped):
        await svc.transition(
            db_session,
            pack_id=pack.id,
            workspace_id=workspace.id,
            target_state=SkillPackState.STALE,
            actor_identity_id=identity.id,
            reason="curator nightly sweep",
            bypass_pinned=False,
            actor_kind="curator",
        )

    await db_session.refresh(pack)
    assert pack.state == SkillPackState.ACTIVE
    assert pack.pinned is True


async def test_user_action_can_bypass_pinned(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    pack.pinned = True
    await db_session.flush()

    result = await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.STALE,
        actor_identity_id=identity.id,
        reason="manual override",
        bypass_pinned=True,
        actor_kind="user",
    )
    assert result.state == SkillPackState.STALE


async def test_pin_tombstoned_pack_raises(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id, state=SkillPackState.TOMBSTONE)
    await db_session.flush()
    with pytest.raises(svc.TerminalStateError):
        await svc.pin_pack(
            db_session,
            pack_id=pack.id,
            workspace_id=workspace.id,
            actor_identity_id=identity.id,
        )
