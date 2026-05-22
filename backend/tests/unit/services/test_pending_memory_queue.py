"""Service tests for the queue side of M0.7 cache-aware mutation.

Cover the default deferred path, the workspace gate that blocks
``effective="now"``, and the always-on hard cap that protects the
system prompt from runaway memory writes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ImmediateMemoryNotPermitted, MemoryHardCapExceeded
from app.db.models.audit import AuditEvent
from app.db.models.memory import MemoryKind, MemoryScope
from app.db.models.pending_memory import (
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.services import memory as memory_svc
from app.services import pending_memory as pending_memory_svc
from app.services import session as session_svc

pytestmark = pytest.mark.asyncio


async def _ensure_session(db_session, workspace, identity):
    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )


async def _audit_actions(db_session, workspace_id) -> list[str]:
    rows = (
        (
            await db_session.execute(
                select(AuditEvent.action).where(AuditEvent.workspace_id == workspace_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_queue_pending_memory_writes_row_and_audits(db_session, workspace, identity):
    sess = await _ensure_session(db_session, workspace, identity)
    row = await pending_memory_svc.queue_pending_memory(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={
            "content": "user prefers tabs over spaces",
            "scope": "user",
            "kind": "semantic",
        },
    )
    assert row.status == PendingMemoryStatus.PENDING
    assert row.workspace_id == workspace.id
    assert row.identity_id == identity.id
    actions = await _audit_actions(db_session, workspace.id)
    assert "pending_memory.queued" in actions


async def test_queue_immediate_or_pending_defaults_to_deferred(db_session, workspace, identity):
    sess = await _ensure_session(db_session, workspace, identity)
    pending, applied = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=None,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={"content": "fact A", "scope": "user", "kind": "semantic"},
    )
    assert pending is not None
    assert applied is None
    assert pending.status == PendingMemoryStatus.PENDING
    actions = await _audit_actions(db_session, workspace.id)
    assert "memory.deferred_to_next_session" in actions


async def test_immediate_blocked_when_workspace_gate_closed(db_session, workspace, identity):
    sess = await _ensure_session(db_session, workspace, identity)
    workspace.home_config_json = {"memory": {"allow_immediate": False}}
    await db_session.flush()

    with pytest.raises(ImmediateMemoryNotPermitted) as exc_info:
        await pending_memory_svc.queue_immediate_or_pending(
            db_session,
            workspace_id=workspace.id,
            session_id=sess.id,
            identity_id=identity.id,
            agent_id=None,
            target_table=PendingMemoryTargetTable.MEMORIES,
            payload={"content": "now-fact", "scope": "user", "kind": "semantic"},
            effective="now",
        )
    assert exc_info.value.code == "memory.immediate_not_permitted"
    actions = await _audit_actions(db_session, workspace.id)
    assert "memory.immediate_not_permitted" in actions


async def test_immediate_succeeds_when_workspace_opts_in(db_session, workspace, identity):
    sess = await _ensure_session(db_session, workspace, identity)
    workspace.home_config_json = {"memory": {"allow_immediate": True}}
    await db_session.flush()

    pending, applied = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=None,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={
            "content": "applied immediately",
            "scope": "user",
            "kind": "semantic",
        },
        effective="now",
    )
    assert pending is None
    assert applied is not None
    assert "id" in applied
    actions = await _audit_actions(db_session, workspace.id)
    assert "memory.applied_immediate" in actions


async def test_apply_payload_enforces_hard_cap(db_session, workspace, identity):
    workspace.home_config_json = {"memory": {"always_on_max_chars": 100}}
    await db_session.flush()

    await memory_svc.apply_payload(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        agent_id=None,
        payload={
            "content": "x" * 90,
            "scope": "user",
            "kind": "semantic",
        },
    )

    with pytest.raises(MemoryHardCapExceeded) as exc_info:
        await memory_svc.apply_payload(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            agent_id=None,
            payload={
                "content": "y" * 50,
                "scope": "user",
                "kind": "semantic",
            },
        )
    assert exc_info.value.code == "memory.hard_cap_exceeded"


async def test_kv_upsert_subtracts_existing_when_checking_cap(db_session, workspace, identity):
    workspace.home_config_json = {"memory": {"always_on_max_chars": 100}}
    await db_session.flush()

    await memory_svc.apply_payload(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        agent_id=None,
        payload={
            "content": "v" * 80,
            "scope": "user",
            "kind": "kv",
            "key": "preferred_editor",
        },
    )
    # Replacing the same key with a slightly larger value still fits.
    row = await memory_svc.apply_payload(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        agent_id=None,
        payload={
            "content": "v" * 95,
            "scope": "user",
            "kind": "kv",
            "key": "preferred_editor",
        },
    )
    assert row.content == "v" * 95
    assert row.scope == MemoryScope.USER
    assert row.kind == MemoryKind.KV
    assert row.key == "preferred_editor"


async def test_apply_payload_rejects_disallowed_scope(db_session, workspace, identity):
    workspace.home_config_json = {"memory": {"permitted_scopes": ["user"]}}
    await db_session.flush()

    with pytest.raises(Exception) as exc_info:
        await memory_svc.apply_payload(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            agent_id=None,
            payload={
                "content": "fact",
                "scope": "workspace",
                "kind": "semantic",
            },
        )
    assert getattr(exc_info.value, "code", "").startswith("memory.")


async def test_workspace_settings_merge_with_platform_defaults(db_session, workspace):
    settings = await memory_svc.get_workspace_memory_settings(db_session, workspace_id=workspace.id)
    assert settings.always_on_max_chars == 4000
    assert settings.allow_immediate is False
    assert "user" in settings.permitted_scopes


async def test_unknown_workspace_falls_back_to_defaults(db_session):
    settings = await memory_svc.get_workspace_memory_settings(db_session, workspace_id=uuid.uuid4())
    assert settings.always_on_max_chars > 0
    assert settings.allow_immediate is False
