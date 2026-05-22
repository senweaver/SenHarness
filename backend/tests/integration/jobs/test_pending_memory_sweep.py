"""Backstop sweep integration tests (M0.7).

The sweep must:

1. promote PENDING rows whose parent session has been quiet for at
   least the configured idle window;
2. leave rows belonging to recently-active sessions for the next
   synchronous capture hook so the two paths don't fight for the
   same row.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.pending_memory import (
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.repositories.pending_memory import PendingMemoryRepository
from app.repositories.session import SessionRepository
from app.services import pending_memory as pending_memory_svc
from app.services import session as session_svc

pytestmark = pytest.mark.asyncio


async def test_sweep_promotes_quiet_session_row(db_session, workspace, identity):
    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    sess.last_message_at = utcnow_naive() - timedelta(minutes=45)
    await db_session.flush()

    pending, _ = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=None,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={"content": "fact", "scope": "user", "kind": "semantic"},
    )
    pending.created_at = utcnow_naive() - timedelta(seconds=3600)
    await db_session.flush()

    result = await pending_memory_svc.promote_pending_memories_workspace_sweep(
        db_session,
        workspace_id=workspace.id,
        max_age_seconds=1800,
    )
    assert result["promoted"] == 1
    await db_session.refresh(pending)
    assert pending.status == PendingMemoryStatus.PROMOTED


async def test_sweep_skips_active_session(db_session, workspace, identity):
    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    sess.last_message_at = utcnow_naive() - timedelta(minutes=2)
    await db_session.flush()

    pending, _ = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=None,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={"content": "later", "scope": "user", "kind": "semantic"},
    )
    pending.created_at = utcnow_naive() - timedelta(seconds=3600)
    await db_session.flush()

    result = await pending_memory_svc.promote_pending_memories_workspace_sweep(
        db_session,
        workspace_id=workspace.id,
        max_age_seconds=1800,
    )
    assert result == {"promoted": 0, "skipped": 0, "failed": 0}
    await db_session.refresh(pending)
    assert pending.status == PendingMemoryStatus.PENDING


async def test_sweep_promotes_when_session_soft_deleted(db_session, workspace, identity):
    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    pending, _ = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=None,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={"content": "fact", "scope": "user", "kind": "semantic"},
    )
    pending.created_at = utcnow_naive() - timedelta(seconds=3600)
    await SessionRepository(db_session).soft_delete(sess)
    await db_session.flush()

    result = await pending_memory_svc.promote_pending_memories_workspace_sweep(
        db_session,
        workspace_id=workspace.id,
        max_age_seconds=1800,
    )
    assert result["promoted"] == 1
    repo = PendingMemoryRepository(db_session)
    refreshed = await repo.get(pending.id)
    assert refreshed is not None
    assert refreshed.status == PendingMemoryStatus.PROMOTED
