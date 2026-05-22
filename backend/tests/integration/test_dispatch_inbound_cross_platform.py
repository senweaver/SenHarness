"""End-to-end dispatcher behaviour across the cross-platform flag.

Covers two contracts:

* ``cross_platform_enabled = False`` (default): the dispatcher must
  take the legacy per-channel path. No ``logical_threads`` row exists
  after a single inbound.
* ``cross_platform_enabled = True``: ``find_or_create_thread_for_inbound``
  resolves an existing binding and reuses its thread / session.

The test stubs the agent runner + provider send_text so we exercise
just the dispatcher flow without spinning up a real LLM call.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.logical_thread import LogicalThread, ThreadChannelBinding
from app.services.system_settings import (
    SystemSettingKey,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def _seed_channel(db, *, workspace_id, agent_id):
    from app.db.models.channel import Channel, ChannelKind

    ch = Channel(
        workspace_id=workspace_id,
        name="test-slack",
        kind=ChannelKind.SLACK,
        inbound_token=uuid.uuid4().hex[:32],
        config_json={},
        default_agent_id=agent_id,
        enabled=True,
    )
    db.add(ch)
    await db.flush()
    return ch


async def test_disabled_cross_platform_keeps_legacy_path(
    db_session, identity, workspace, agent, monkeypatch
):
    """Default contract: no logical_thread row is ever created."""
    ch = await _seed_channel(db_session, workspace_id=workspace.id, agent_id=agent.id)

    # Stub the runner so we don't hit a real LLM.
    from app.services import agent_runner as runner

    async def fake_run(**_kw):
        return runner.AgentResult(final_text="hi", session_id=uuid.uuid4())

    async def fake_ensure(db, **kw):
        from app.db.models.session import SessionKind
        from app.repositories.session import SessionRepository

        return await SessionRepository(db).create(
            workspace_id=kw["workspace_id"],
            kind=SessionKind.CHANNEL,
            subject_id=kw["subject_id"],
            channel_id=kw["channel_id"],
            metadata_json={"thread_key": kw["thread_key"]},
        )

    monkeypatch.setattr(runner, "run_agent_one_shot", fake_run)
    monkeypatch.setattr(runner, "ensure_channel_session", fake_ensure)
    # Skip the outbound provider call entirely.
    from app.services import channels as ch_mod

    class _StubProvider:
        async def send_text(self, **_kw):
            return None

    monkeypatch.setattr(ch_mod, "get_provider", lambda _kind: _StubProvider())

    # Override get_session_factory so the stub flow uses our fixture session.
    from app.db import session as db_session_mod
    from app.services.channel_dispatch import dispatch_inbound
    from app.services.channels.base import InboundMessage

    class _Factory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(_inner):  # noqa: N805
                    return db_session

                async def __aexit__(_inner, *args):  # noqa: N805
                    return False

            return _Ctx()

    monkeypatch.setattr(db_session_mod, "get_session_factory", lambda: _Factory())

    await dispatch_inbound(
        channel_id=ch.id,
        inbound=InboundMessage(
            thread_key="T1.C1",
            user_text="hello",
            external_user="alice@example.com",
        ),
    )

    rows = (
        (
            await db_session.execute(
                select(LogicalThread).where(LogicalThread.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == [], "default-disabled flag must never create a thread"


async def test_enabled_with_existing_binding_resumes_thread(
    db_session, identity, workspace, agent, monkeypatch
):
    """Pre-paired binding routes a fresh inbound back to the same thread."""
    ch = await _seed_channel(db_session, workspace_id=workspace.id, agent_id=agent.id)

    # Flip the platform default to enabled. Workspace has no override.
    await set_system_setting(
        db_session,
        SystemSettingKey.SESSION_ROUTING_DEFAULTS,
        {"cross_platform_enabled": True},
    )

    # Pre-create a thread + binding for the inbound sender so the
    # dispatcher hits the resume branch (not the create branch).
    from app.db.models.session import SessionKind
    from app.repositories.session import SessionRepository

    seeded_session = await SessionRepository(db_session).create(
        workspace_id=workspace.id,
        kind=SessionKind.CHANNEL,
        subject_id=agent.id,
        channel_id=ch.id,
        owner_identity_id=identity.id,
        title="seed",
    )
    await db_session.flush()

    thread = LogicalThread(
        workspace_id=workspace.id,
        identity_id=identity.id,
        agent_id=agent.id,
        primary_session_id=seeded_session.id,
    )
    db_session.add(thread)
    await db_session.flush()

    binding = ThreadChannelBinding(
        workspace_id=workspace.id,
        thread_id=thread.id,
        channel_id=ch.id,
        external_user_id="alice@example.com",
        is_paired=True,
    )
    db_session.add(binding)
    await db_session.flush()

    # Stubs match the disabled-path test.
    from app.services import agent_runner as runner

    async def fake_run(**_kw):
        return runner.AgentResult(final_text="hi", session_id=seeded_session.id)

    async def fake_ensure(*_args, **_kw):
        # Should NOT be called when the cross-platform path resolves
        # the session via the binding row.
        raise AssertionError("ensure_channel_session called on cross-platform path")

    monkeypatch.setattr(runner, "run_agent_one_shot", fake_run)
    monkeypatch.setattr(runner, "ensure_channel_session", fake_ensure)
    from app.services import channels as ch_mod

    class _StubProvider:
        async def send_text(self, **_kw):
            return None

    monkeypatch.setattr(ch_mod, "get_provider", lambda _kind: _StubProvider())

    from app.db import session as db_session_mod

    class _Factory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(_inner):  # noqa: N805
                    return db_session

                async def __aexit__(_inner, *args):  # noqa: N805
                    return False

            return _Ctx()

    monkeypatch.setattr(db_session_mod, "get_session_factory", lambda: _Factory())

    from app.services.channel_dispatch import dispatch_inbound
    from app.services.channels.base import InboundMessage

    await dispatch_inbound(
        channel_id=ch.id,
        inbound=InboundMessage(
            thread_key="T1.C1",
            user_text="hello again",
            external_user="alice@example.com",
        ),
    )

    # No new thread created — binding resolves to the seed thread.
    rows = (
        (
            await db_session.execute(
                select(LogicalThread).where(LogicalThread.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == thread.id
