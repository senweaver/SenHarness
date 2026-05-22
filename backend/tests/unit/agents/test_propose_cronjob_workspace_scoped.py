"""Workspace-scoping coverage for ``run_propose_cronjob`` (M2.8).

Three rejection branches the runner must enforce:

1. ``target_agent_id`` belongs to a different workspace → reject with
   ``code='evolver.cross_workspace_agent'`` and a
   ``evolver.cronjob_rejected`` audit row tagged
   ``code='cross_workspace_agent'``.
2. Any of ``delivery_channel_ids`` belongs to a different workspace →
   reject with ``code='evolver.cross_workspace_channel'``.
3. Workspace has the evolver disabled → reject before any DB write
   (no Approval row, no Channel/Agent lookup) with
   ``code='evolver.disabled'``.

These DB-backed tests use the shared ``db_session`` fixture and reach
into the runner via ``set_context`` + a patched session factory. When
Postgres isn't available locally the fixtures gracefully skip.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.cronjob_propose import (
    AUDIT_REJECTED,
    ProposeCronjobArgs,
    run_propose_cronjob,
)
from app.db.models.approval import Approval, ApprovalResourceType
from app.db.models.audit import AuditEvent

pytestmark = pytest.mark.asyncio


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _set_ctx(workspace, identity, *, agent_id=None):
    set_context(
        ToolRunContext(
            run_id=uuid.uuid4(),
            workspace_id=workspace.id,
            session_id=uuid.uuid4(),
            identity_id=identity.id,
            agent_id=agent_id or uuid.uuid4(),
            scratch_base=Path("/tmp"),
        )
    )


async def _enable_evolver(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


async def _make_agent_in(db_session, *, workspace, identity):
    from app.services import agent as svc

    a = await svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name=f"Agent-{uuid.uuid4().hex[:6]}",
        description="cronjob target",
        persona_md="You are a test target.",
    )
    await db_session.flush()
    return a


async def _make_channel_in(db_session, *, workspace, identity):
    from app.db.models.channel import Channel

    ch = Channel(
        workspace_id=workspace.id,
        created_by=identity.id,
        name=f"chan-{uuid.uuid4().hex[:6]}",
        kind="webhook",
        inbound_token=uuid.uuid4().hex,
        config_json={},
        metadata_json={},
        sender_allowlist_json={},
        enabled=True,
    )
    db_session.add(ch)
    await db_session.flush()
    return ch


async def test_target_agent_in_other_workspace_rejected(
    db_session, workspace, identity, monkeypatch
):
    await _enable_evolver(db_session, workspace)

    from app.services import workspace as ws_svc

    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other-{uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    foreign_agent = await _make_agent_in(db_session, workspace=other_ws, identity=identity)

    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.cronjob_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_cronjob(
        ProposeCronjobArgs(
            name="cross-ws",
            schedule="0 9 * * *",
            prompt_template="read me",
            target_agent_id=foreign_agent.id,
            rationale="hostile attempt",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.cross_workspace_agent"

    # No Approval row should land for the rejected proposal.
    pending = list(
        (
            await db_session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace.id,
                    Approval.resource_type == ApprovalResourceType.FLOW_CREATE.value,
                )
            )
        ).scalars()
    )
    assert pending == []

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_REJECTED,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["code"] == "cross_workspace_agent"


async def test_delivery_channel_in_other_workspace_rejected(
    db_session, workspace, identity, agent, monkeypatch
):
    await _enable_evolver(db_session, workspace)

    from app.services import workspace as ws_svc

    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other-{uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    foreign_channel = await _make_channel_in(db_session, workspace=other_ws, identity=identity)

    _set_ctx(workspace, identity, agent_id=agent.id)
    monkeypatch.setattr(
        "app.agents.tools.cronjob_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_cronjob(
        ProposeCronjobArgs(
            name="cross-channel",
            schedule="every 1h",
            prompt_template="read me",
            delivery_channel_ids=[foreign_channel.id],
            rationale="hostile channel",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.cross_workspace_channel"
    assert str(foreign_channel.id) in result["missing_channel_ids"]

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_REJECTED,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["code"] == "cross_workspace_channel"


async def test_workspace_disabled_rejected_before_any_db_write(
    db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": False}}
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.cronjob_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_cronjob(
        ProposeCronjobArgs(
            name="disabled-attempt",
            schedule="0 9 * * *",
            prompt_template="x",
            rationale="x",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.disabled"

    pending = list(
        (
            await db_session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace.id,
                    Approval.resource_type == ApprovalResourceType.FLOW_CREATE.value,
                )
            )
        ).scalars()
    )
    assert pending == []

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_REJECTED,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["code"] == "evolver.disabled"
