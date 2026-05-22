"""M1.5 failure-safety: telemetry breakage must not break capture.

Three failure surfaces are pinned here:

1. ``record_usage_batch`` raises → capture row still lands and an
   ``audit_events(action="skill.usage_recording_failed")`` row is
   written so the rollup pipeline has a breadcrumb.
2. The judge enqueue path (M0.3) keeps working — we mock the queue
   and assert it was called with the captured artifact id even though
   skill telemetry blew up.
3. The pending memory promote path (M0.7) still drains its buffer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from app.agents.kernels.base import (
    BackendCapabilities,
    RunEvent,
    RunEventKind,
    RunRequest,
)
from app.db.models.audit import AuditEvent
from app.db.models.pending_memory import (
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.repositories.pending_memory import PendingMemoryRepository

pytestmark = pytest.mark.asyncio


class _BackendWithInjection:
    backend_kind = "native"

    def __init__(self, events: list[RunEvent], injected: list[uuid.UUID]) -> None:
        self._events = events
        self._injected = injected
        self._captured_run_id: uuid.UUID | None = None

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        self._captured_run_id = req.run_id
        for ev in self._events:
            yield ev

    async def cancel(self, _run_id: uuid.UUID) -> None:
        return

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def get_injected_skill_ids(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        if run_id == self._captured_run_id:
            return list(self._injected)
        return []


async def _seed_pack(db_session, workspace) -> SkillPack:
    pack = SkillPack(
        workspace_id=workspace.id,
        slug=f"failsafe-{uuid.uuid4().hex[:6]}",
        name="failsafe pack",
        description="failsafe pack",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        source=SkillPackSource.WORKSPACE,
        state=SkillPackState.ACTIVE,
    )
    db_session.add(pack)
    await db_session.flush([pack])
    return pack


async def test_record_usage_failure_does_not_break_capture(
    db_session, workspace, identity, agent, monkeypatch
):
    pack = await _seed_pack(db_session, workspace)

    from app.services import agent_runner
    from app.services import session as session_svc
    from app.services import skill_usage as skill_usage_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    pending, _ = await _queue_pending_memory(db_session, workspace, identity, agent, sess)
    await db_session.flush()
    assert pending.status == PendingMemoryStatus.PENDING

    enqueue_calls: list[tuple] = []

    async def _fake_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return None

    monkeypatch.setattr("app.worker.queue.enqueue", _fake_enqueue, raising=False)

    async def _exploding_record_usage_batch(*_args, **_kwargs):
        raise RuntimeError("simulated telemetry outage")

    monkeypatch.setattr(skill_usage_svc, "record_usage_batch", _exploding_record_usage_batch)

    events = [
        RunEvent(RunEventKind.DELTA, {"text": "Got it."}),
        RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(uuid.uuid4()), "text": "Got it."},
        ),
    ]
    backend = _BackendWithInjection(events, injected=[pack.id])
    monkeypatch.setattr(agent_runner, "get_backend", lambda _kind: backend)

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="please remember I prefer markdown",
    )
    assert result.error is None
    await db_session.commit()

    from app.services import session_artifact as artifact_svc

    captured_run_id = backend._captured_run_id
    assert captured_run_id is not None
    artifact = await artifact_svc.get_artifact(
        db_session, workspace_id=workspace.id, run_id=captured_run_id
    )
    assert artifact is not None, "capture must survive a telemetry outage"
    assert artifact.injected_skill_pack_ids == [str(pack.id)]

    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill.usage_recording_failed",
                    AuditEvent.resource_id == captured_run_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["pack_count"] == 1
    assert audits[0].metadata_json["error_class"] == "RuntimeError"

    judge_args = [c[0] for c in enqueue_calls]
    assert any(
        a and a[0] == "judge_session_artifact" and a[1] == str(artifact.id) for a in judge_args
    ), f"M0.3 judge path must still enqueue (got {judge_args!r})"

    repo = PendingMemoryRepository(db_session)
    refreshed = await repo.get(pending.id)
    assert refreshed is not None
    assert refreshed.status == PendingMemoryStatus.PROMOTED, (
        "M0.7 promote hook must still drain the pending memory queue"
    )


async def _queue_pending_memory(db_session, workspace, identity, agent, sess):
    from app.services import pending_memory as pending_memory_svc

    return await pending_memory_svc.queue_immediate_or_pending(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        identity_id=identity.id,
        agent_id=agent.id,
        target_table=PendingMemoryTargetTable.MEMORIES,
        payload={
            "content": "user prefers markdown",
            "scope": "user",
            "kind": "kv",
            "key": "preferred_format",
        },
    )
