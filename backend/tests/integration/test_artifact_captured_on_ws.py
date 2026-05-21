"""End-to-end smoke test for M0.2 artifact capture on the channel/flow path.

We hit ``run_agent_one_shot`` (the same code path channels and flows
use, and the function the WS turn handler delegates the artifact bits
to) with a mock backend that emits a tiny event stream. Then we assert
the ``session_artifacts`` row landed and the audit row was written.

The WS handler itself isn't exercised here — that requires an actual
WebSocket harness which the project doesn't ship yet — but the capture
helper is shared between both paths so this test pins the same wire.
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
from app.services import session_artifact as artifact_svc

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


async def test_run_agent_one_shot_captures_artifact(
    db_session, workspace, identity, agent, monkeypatch
):
    from app.services import agent_runner

    final_msg_id = uuid.uuid4()
    backend = _MockBackend(
        [
            RunEvent(RunEventKind.DELTA, {"text": "Hello "}),
            RunEvent(RunEventKind.TOOL_CALL, {"id": "1", "name": "search", "args": {"q": "x"}}),
            RunEvent(RunEventKind.TOOL_RESULT, {"id": "1", "result": ["a"]}),
            RunEvent(RunEventKind.DELTA, {"text": "world"}),
            RunEvent(
                RunEventKind.USAGE,
                {"tokens": {"input": 10, "output": 20}, "cost": 0.001},
            ),
            RunEvent(RunEventKind.FINAL, {"message_id": str(final_msg_id), "text": "Hello world"}),
        ]
    )

    monkeypatch.setattr(
        agent_runner,
        "get_backend",
        lambda _kind: backend,
    )

    # Create a session up front so the runner finds it.
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="hi there",
    )
    assert result.error is None
    assert result.final_text == "Hello world"
    await db_session.commit()

    rows = await artifact_svc.list_artifacts_for_session(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
    )
    assert len(rows) == 1
    art = rows[0]
    assert art.iteration_count == 2
    assert art.invoked_tools == ["search"]
    assert art.final_outcome == "success"
    # The FINAL frame's message_id was overwritten with the persisted
    # assistant message id; the last assistant turn must hold a non-null
    # lineage pointer.
    assistant_turns = [t for t in art.turns_json if t["role"] == "assistant"]
    assert assistant_turns
    assert assistant_turns[-1]["message_id"] is not None


async def test_capture_audit_row_emitted(
    db_session, workspace, identity
):
    """``capture_artifact`` emits ``audit_events(action="artifact.captured")``."""
    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    run_id = uuid.uuid4()
    await artifact_svc.capture_artifact(
        db_session,
        run_id=run_id,
        workspace_id=workspace.id,
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        user_text="q",
        events=[],
        final_outcome="success",
    )
    await db_session.flush()
    stmt = select(AuditEvent).where(
        AuditEvent.workspace_id == workspace.id,
        AuditEvent.action == "artifact.captured",
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].metadata_json["run_id"] == str(run_id)
