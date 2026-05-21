"""Unit: ``skill_lifecycle.transition`` edge whitelist (M1.1).

Covers the 14 edges in ``ALLOWED_TRANSITIONS`` plus the two
"forbidden by construction" cases (terminal TOMBSTONE + non-listed
target). Also asserts the audit row + ``state_changed_at/by`` columns
are populated on every successful transition.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.skills import SkillPack, SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_lifecycle as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(
    db, *, workspace_id, slug: str | None = None, state: SkillPackState = SkillPackState.ACTIVE
) -> SkillPack:
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=slug or f"sk-{uuid.uuid4().hex[:6]}",
        name="Test pack",
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
    await db.flush()
    return pack


# ── Edge coverage matrix ────────────────────────────────────
HAPPY_EDGES = [
    (SkillPackState.DRAFT, SkillPackState.CANDIDATE),
    (SkillPackState.DRAFT, SkillPackState.ARCHIVED),
    (SkillPackState.CANDIDATE, SkillPackState.ACTIVE),
    (SkillPackState.CANDIDATE, SkillPackState.REJECTED),
    (SkillPackState.ACTIVE, SkillPackState.STALE),
    (SkillPackState.ACTIVE, SkillPackState.PINNED),
    (SkillPackState.ACTIVE, SkillPackState.SUPERSEDED),
    (SkillPackState.ACTIVE, SkillPackState.DEPRECATED),
    (SkillPackState.ACTIVE, SkillPackState.ARCHIVED),
    (SkillPackState.STALE, SkillPackState.ACTIVE),
    (SkillPackState.STALE, SkillPackState.ARCHIVED),
    (SkillPackState.STALE, SkillPackState.PINNED),
    (SkillPackState.PINNED, SkillPackState.ACTIVE),
    (SkillPackState.DEPRECATED, SkillPackState.ARCHIVED),
    (SkillPackState.SUPERSEDED, SkillPackState.ARCHIVED),
    (SkillPackState.ARCHIVED, SkillPackState.ACTIVE),
    (SkillPackState.ARCHIVED, SkillPackState.TOMBSTONE),
    (SkillPackState.REJECTED, SkillPackState.TOMBSTONE),
]


@pytest.mark.parametrize("from_state, to_state", HAPPY_EDGES)
async def test_allowed_edges_succeed(db_session, workspace, identity, from_state, to_state):
    pack = await _make_pack(db_session, workspace_id=workspace.id, state=from_state)
    result = await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=to_state,
        actor_identity_id=identity.id,
        reason="unit test",
        bypass_pinned=True,
    )
    assert result.state == to_state
    assert result.state_changed_at is not None
    assert result.state_changed_by == identity.id


async def test_forbidden_edge_raises_invalid(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id, state=SkillPackState.DRAFT)
    with pytest.raises(svc.InvalidStateTransition) as exc:
        await svc.transition(
            db_session,
            pack_id=pack.id,
            workspace_id=workspace.id,
            target_state=SkillPackState.ACTIVE,
            actor_identity_id=identity.id,
            reason="trying impossible edge",
        )
    extras = exc.value.extras
    assert extras["from"] == "draft"
    assert extras["to"] == "active"
    assert "candidate" in extras["allowed"]


async def test_terminal_tombstone_raises(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id, state=SkillPackState.TOMBSTONE)
    with pytest.raises(svc.TerminalStateError):
        await svc.transition(
            db_session,
            pack_id=pack.id,
            workspace_id=workspace.id,
            target_state=SkillPackState.ACTIVE,
            actor_identity_id=identity.id,
            reason="resurrection forbidden",
            bypass_pinned=True,
        )


async def test_audit_row_written_on_transition(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id, state=SkillPackState.ACTIVE)
    await svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.STALE,
        actor_identity_id=identity.id,
        reason="audit smoke test",
    )
    row = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill.transitioned",
                    AuditEvent.resource_id == pack.id,
                )
                .order_by(AuditEvent.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    meta = row.metadata_json
    assert meta["from"] == "active"
    assert meta["to"] == "stale"
    assert meta["reason"] == "audit smoke test"
    assert meta["pack_id"] == str(pack.id)


async def test_unknown_pack_raises_not_found(db_session, workspace, identity):
    fake_id = uuid.uuid4()
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        await svc.transition(
            db_session,
            pack_id=fake_id,
            workspace_id=workspace.id,
            target_state=SkillPackState.STALE,
            actor_identity_id=identity.id,
            reason="missing pack",
        )
