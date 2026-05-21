"""Verify ``trigger_flow`` routes by ``execution_mode``.

We patch the three downstream coroutines so the dispatch decision is
the only thing under test. DB-backed flow rows feed in via the
standard fixtures.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models.flow import FlowExecutionMode, FlowTriggerKind
from app.repositories.flow import FlowRepository
from app.services import flow as flow_svc


async def _make_flow(db_session, workspace, agent, mode, **overrides):
    repo = FlowRepository(db_session)
    flow = await repo.create(
        workspace_id=workspace.id,
        name=overrides.get("name", "test-flow"),
        description=None,
        trigger_kind=FlowTriggerKind.MANUAL,
        trigger_config=overrides.get("trigger_config", {}),
        execution_mode=mode,
        agent_id=agent.id,
        prompt_template="ignored",
        graph_json={},
        enabled=True,
    )
    await db_session.flush()
    await db_session.commit()
    return flow


async def test_agent_mode_dispatches_to_agent_path(db_session, workspace, agent):
    flow = await _make_flow(db_session, workspace, agent, FlowExecutionMode.AGENT)
    fake_id = uuid.uuid4()
    with (
        patch.object(flow_svc, "_run_agent_flow", AsyncMock(return_value=fake_id)) as m_agent,
        patch.object(flow_svc, "_run_script_flow", AsyncMock()) as m_script,
        patch.object(flow_svc, "_run_http_flow", AsyncMock()) as m_http,
    ):
        run_id = await flow_svc.trigger_flow(
            flow.id,
            workspace_id=workspace.id,
            trigger_kind=FlowTriggerKind.MANUAL,
        )
    assert run_id == fake_id
    m_agent.assert_awaited_once()
    m_script.assert_not_awaited()
    m_http.assert_not_awaited()


async def test_script_mode_dispatches_to_script_path(db_session, workspace, agent):
    flow = await _make_flow(
        db_session,
        workspace,
        agent,
        FlowExecutionMode.NO_AGENT_SCRIPT,
        trigger_config={"script_command": "echo hi"},
    )
    fake_id = uuid.uuid4()
    with (
        patch.object(flow_svc, "_run_agent_flow", AsyncMock()) as m_agent,
        patch.object(flow_svc, "_run_script_flow", AsyncMock(return_value=fake_id)) as m_script,
        patch.object(flow_svc, "_run_http_flow", AsyncMock()) as m_http,
    ):
        run_id = await flow_svc.trigger_flow(
            flow.id,
            workspace_id=workspace.id,
            trigger_kind=FlowTriggerKind.MANUAL,
        )
    assert run_id == fake_id
    m_script.assert_awaited_once()
    m_agent.assert_not_awaited()
    m_http.assert_not_awaited()


async def test_http_mode_dispatches_to_http_path(db_session, workspace, agent):
    flow = await _make_flow(
        db_session,
        workspace,
        agent,
        FlowExecutionMode.NO_AGENT_HTTP,
        trigger_config={"http_url": "https://example.com/health"},
    )
    fake_id = uuid.uuid4()
    with (
        patch.object(flow_svc, "_run_agent_flow", AsyncMock()) as m_agent,
        patch.object(flow_svc, "_run_script_flow", AsyncMock()) as m_script,
        patch.object(flow_svc, "_run_http_flow", AsyncMock(return_value=fake_id)) as m_http,
    ):
        run_id = await flow_svc.trigger_flow(
            flow.id,
            workspace_id=workspace.id,
            trigger_kind=FlowTriggerKind.MANUAL,
        )
    assert run_id == fake_id
    m_http.assert_awaited_once()
    m_script.assert_not_awaited()
    m_agent.assert_not_awaited()


async def test_disabled_flow_rejected(db_session, workspace, agent):
    flow = await _make_flow(db_session, workspace, agent, FlowExecutionMode.AGENT)
    flow.enabled = False
    await db_session.flush()
    await db_session.commit()
    with pytest.raises(Exception) as exc:
        await flow_svc.trigger_flow(
            flow.id,
            workspace_id=workspace.id,
            trigger_kind=FlowTriggerKind.MANUAL,
        )
    assert "disabled" in (getattr(exc.value, "code", "") or str(exc.value)).lower()
