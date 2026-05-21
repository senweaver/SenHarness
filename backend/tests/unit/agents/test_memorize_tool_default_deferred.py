"""Agent-tool tests for ``memorize`` (M0.7).

Cover the cache-aware default (``effective="next_session"``), the
workspace gate that rejects ``effective="now"`` without policy opt-in,
and the immediate apply path when the gate is open. The tool is
exercised via :func:`run_memorize`, which sets up its own DB session
through ``get_session_factory`` — we patch the context + session
factory to inject the test fixtures.
"""

from __future__ import annotations

import pytest

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.memory import MemorizeArgs, run_memorize

pytestmark = pytest.mark.asyncio


def _set_ctx(workspace, identity, session_id):
    import uuid
    from pathlib import Path

    ctx = ToolRunContext(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=session_id,
        identity_id=identity.id,
        agent_id=uuid.uuid4(),
        scratch_base=Path("/tmp"),
    )
    set_context(ctx)


async def _factory_returning(db_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def test_memorize_default_returns_deferred(
    db_session, workspace, identity, monkeypatch
):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    _set_ctx(workspace, identity, sess.id)
    factory = await _factory_returning(db_session)
    monkeypatch.setattr(
        "app.agents.tools.memory.get_session_factory", lambda: factory
    )

    out = await run_memorize(
        MemorizeArgs(content="user prefers dark mode")
    )
    assert out["status"] == "deferred"
    assert out["effective"] == "next_session"
    assert "pending_memory_id" in out


async def test_memorize_now_rejected_when_gate_closed(
    db_session, workspace, identity, monkeypatch
):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    workspace.home_config_json = {"memory": {"allow_immediate": False}}
    await db_session.flush()
    _set_ctx(workspace, identity, sess.id)
    factory = await _factory_returning(db_session)
    monkeypatch.setattr(
        "app.agents.tools.memory.get_session_factory", lambda: factory
    )

    out = await run_memorize(
        MemorizeArgs(
            content="apply now",
            effective="now",
        )
    )
    assert out["status"] == "rejected"
    assert out["code"] == "memory.immediate_not_permitted"


async def test_memorize_now_succeeds_when_gate_open(
    db_session, workspace, identity, monkeypatch
):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    workspace.home_config_json = {"memory": {"allow_immediate": True}}
    await db_session.flush()
    _set_ctx(workspace, identity, sess.id)
    factory = await _factory_returning(db_session)
    monkeypatch.setattr(
        "app.agents.tools.memory.get_session_factory", lambda: factory
    )

    out = await run_memorize(
        MemorizeArgs(
            content="apply now",
            effective="now",
        )
    )
    assert out["status"] == "applied"
    assert out["effective"] == "now"
    assert "memory_id" in out


async def test_memorize_kv_without_key_is_rejected(
    db_session, workspace, identity, monkeypatch
):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    _set_ctx(workspace, identity, sess.id)
    factory = await _factory_returning(db_session)
    monkeypatch.setattr(
        "app.agents.tools.memory.get_session_factory", lambda: factory
    )

    out = await run_memorize(MemorizeArgs(content="x", kind="kv"))
    assert out["status"] == "rejected"
    assert out["code"] == "memory.kv_requires_key"
