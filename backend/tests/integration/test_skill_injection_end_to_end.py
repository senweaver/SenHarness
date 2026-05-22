"""End-to-end M1.5 trace: backend stash → artifact + skill_usage rows.

Walks the canonical journey for the channel/flow path (the WS path
shares the same capture helper, just inside its own factory):

1. Seed a workspace + agent + two ACTIVE SkillPacks.
2. Bind both pack ids onto ``agent.metadata_json["skills"]``.
3. Drive ``run_agent_one_shot`` with a mock backend that exposes the
   M1.7 ``get_injected_skill_ids`` shape.
4. Assert that:
   - ``session_artifacts.injected_skill_pack_ids`` carries both ids.
   - ``skill_usage`` has two rows (event_kind=INJECTED, run_id, pack_id)
     and both reference the same ``run_id``.
   - ``audit_events`` has one ``skill.usage_batch_recorded`` row with
     ``batch_size=2``.
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
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState

pytestmark = pytest.mark.asyncio


class _NativeLikeBackend:
    """Mock backend that exposes ``get_injected_skill_ids`` like NativeBackend."""

    backend_kind = "native"

    def __init__(
        self,
        events: list[RunEvent],
        injected: dict[uuid.UUID, list[uuid.UUID]] | None = None,
    ) -> None:
        self._events = events
        self._injected = injected or {}

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        for ev in self._events:
            yield ev

    async def cancel(self, _run_id: uuid.UUID) -> None:
        return

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def get_injected_skill_ids(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self._injected.get(run_id, []))


async def _seed_pack(db_session, workspace, slug: str) -> SkillPack:
    pack = SkillPack(
        workspace_id=workspace.id,
        slug=slug,
        name=slug,
        description=f"{slug} description",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        source=SkillPackSource.WORKSPACE,
        state=SkillPackState.ACTIVE,
    )
    db_session.add(pack)
    await db_session.flush([pack])
    return pack


async def test_run_writes_artifact_pack_ids_and_skill_usage_rows(
    db_session, workspace, identity, agent, monkeypatch
):
    p1 = await _seed_pack(db_session, workspace, slug="capture-pack-a")
    p2 = await _seed_pack(db_session, workspace, slug="capture-pack-b")

    from app.services import agent_runner
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    final_msg_id = uuid.uuid4()
    events = [
        RunEvent(RunEventKind.DELTA, {"text": "Hi."}),
        RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(final_msg_id), "text": "Hi."},
        ),
    ]

    captured_run_ids: list[uuid.UUID] = []

    class _CapturingBackend(_NativeLikeBackend):
        async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
            captured_run_ids.append(req.run_id)
            self._injected[req.run_id] = [p1.id, p2.id]
            for ev in self._events:
                yield ev

    backend = _CapturingBackend(events)
    monkeypatch.setattr(agent_runner, "get_backend", lambda _kind: backend)

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="hi there",
    )
    assert result.error is None
    assert len(captured_run_ids) == 1
    run_id = captured_run_ids[0]
    await db_session.commit()

    from app.services import session_artifact as artifact_svc

    artifact = await artifact_svc.get_artifact(db_session, workspace_id=workspace.id, run_id=run_id)
    assert artifact is not None
    assert set(artifact.injected_skill_pack_ids) == {str(p1.id), str(p2.id)}

    usage_rows = (
        (
            await db_session.execute(
                select(SkillUsage).where(
                    SkillUsage.workspace_id == workspace.id,
                    SkillUsage.run_id == run_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(usage_rows) == 2
    assert {row.pack_id for row in usage_rows} == {p1.id, p2.id}
    assert all(row.event_kind == SkillUsageEventKind.INJECTED for row in usage_rows)
    assert all(row.session_id == sess.id for row in usage_rows)

    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill.usage_batch_recorded",
                    AuditEvent.resource_id == run_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["batch_size"] == 2
    assert audits[0].metadata_json["event_kind"] == "injected"


async def test_run_without_injected_packs_writes_no_usage_rows(
    db_session, workspace, identity, agent, monkeypatch
):
    """Empty backend stash → artifact captured, no SkillUsage rows."""
    from app.services import agent_runner
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    final_msg_id = uuid.uuid4()
    events = [
        RunEvent(RunEventKind.DELTA, {"text": "ok"}),
        RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(final_msg_id), "text": "ok"},
        ),
    ]
    backend = _NativeLikeBackend(events, injected={})
    monkeypatch.setattr(agent_runner, "get_backend", lambda _kind: backend)

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="ping",
    )
    assert result.error is None
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(SkillUsage).where(SkillUsage.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []

    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "skill.usage_batch_recorded",
                )
            )
        )
        .scalars()
        .all()
    )
    assert audits == []


async def test_run_with_non_native_backend_does_not_break_capture(
    db_session, workspace, identity, agent, monkeypatch
):
    """Backend without ``get_injected_skill_ids`` (e.g. OpenClaw) →
    artifact captured, ``injected_skill_pack_ids`` empty list, no
    telemetry rows."""

    class _RemoteBackend:
        backend_kind = "remote"

        async def run(self, _req: RunRequest) -> AsyncIterator[RunEvent]:
            yield RunEvent(RunEventKind.DELTA, {"text": "ok"})
            yield RunEvent(
                RunEventKind.FINAL,
                {"message_id": str(uuid.uuid4()), "text": "ok"},
            )

        async def cancel(self, _run_id: uuid.UUID) -> None:
            return

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities()

    from app.services import agent_runner
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    monkeypatch.setattr(agent_runner, "get_backend", lambda _kind: _RemoteBackend())

    result = await agent_runner.run_agent_one_shot(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=sess.id,
        identity_id=identity.id,
        user_text="hello",
    )
    assert result.error is None
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(SkillUsage).where(SkillUsage.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
