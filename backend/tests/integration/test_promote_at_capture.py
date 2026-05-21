"""End-to-end test: M0.7 promote hook fires after channel-path capture.

Drives ``run_agent_one_shot`` with a mock backend that emits a tiny
event stream, and seeds a PENDING memory row before the run. After
the run completes, the promote hook should have flipped the row to
PROMOTED and produced a corresponding ``memories`` row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.agents.kernels.base import (
    BackendCapabilities,
    RunEvent,
    RunEventKind,
    RunRequest,
)
from app.db.models.memory import Memory, MemoryScope
from app.db.models.pending_memory import (
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.repositories.pending_memory import PendingMemoryRepository
from app.services import pending_memory as pending_memory_svc

pytestmark = pytest.mark.asyncio


class _MockBackend:
    backend_kind = "native"

    def __init__(self, events: list[RunEvent]) -> None:
        self._events = events

    async def run(self, _req: RunRequest) -> AsyncIterator[RunEvent]:
        for ev in self._events:
            yield ev

    async def cancel(self, _run_id: uuid.UUID) -> None:
        return

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()


async def test_capture_hook_promotes_pending_memories(
    db_session, workspace, identity, agent, monkeypatch
):
    from app.services import agent_runner
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    pending, _ = await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=agent.id,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={
            "content": "user prefers vim",
            "scope": "user",
            "kind": "kv",
            "key": "preferred_editor",
        },
    )
    await db_session.flush()
    assert pending.status == PendingMemoryStatus.PENDING

    final_msg_id = uuid.uuid4()
    backend = _MockBackend(
        [
            RunEvent(RunEventKind.DELTA, {"text": "Got it."}),
            RunEvent(
                RunEventKind.FINAL,
                {"message_id": str(final_msg_id), "text": "Got it."},
            ),
        ]
    )
    monkeypatch.setattr(agent_runner, "get_backend", lambda _kind: backend)

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="please remember I use vim",
    )
    assert result.error is None
    await db_session.commit()

    repo = PendingMemoryRepository(db_session)
    refreshed = await repo.get(pending.id)
    assert refreshed is not None
    assert refreshed.status == PendingMemoryStatus.PROMOTED
    assert refreshed.promoted_target_id is not None

    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(Memory).where(
                Memory.workspace_id == workspace.id,
                Memory.scope == MemoryScope.USER,
                Memory.key == "preferred_editor",
            )
        )
    ).scalars().all()
    assert any(r.content == "user prefers vim" for r in rows)
