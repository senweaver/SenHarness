"""Coverage for ``_run_http_flow`` outcomes.

We mock ``_execute_http`` (and the SSRF guard via patching
``resolve_safe_url``) so the flow row + audit assertions can run
without httpx or DNS.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.url_safety import UnsafeURLError
from app.db.models.flow import (
    FlowExecutionMode,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.services import flow as flow_svc


async def _make_http_flow(db_session, workspace, agent, **cfg):
    repo = FlowRepository(db_session)
    flow = await repo.create(
        workspace_id=workspace.id,
        name="http-flow",
        description=None,
        trigger_kind=FlowTriggerKind.MANUAL,
        trigger_config={"http_url": "https://example.com/health", **cfg},
        execution_mode=FlowExecutionMode.NO_AGENT_HTTP,
        agent_id=agent.id,
        prompt_template="",
        graph_json={},
        enabled=True,
    )
    await db_session.flush()
    await db_session.commit()
    return flow


async def _read_run(db_session, run_id):
    return await FlowRunRepository(db_session).get(run_id)


async def test_silent_2xx_records_outcome(db_session, workspace, agent):
    flow = await _make_http_flow(db_session, workspace, agent)
    with (
        patch.object(
            flow_svc,
            "resolve_safe_url",
            return_value=("https://example.com/health", "93.184.216.34"),
        ),
        patch.object(
            flow_svc,
            "_execute_http",
            AsyncMock(return_value=(FlowRunOutcome.SILENT_2XX, 200, None, 70)),
        ),
    ):
        run_id = await flow_svc._run_http_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.SILENT_2XX
    assert run.status == FlowRunStatus.SUCCEEDED
    assert run.probe_response_status == 200
    assert run.probe_duration_ms == 70


async def test_http_error_when_no_escalation(db_session, workspace, agent):
    flow = await _make_http_flow(
        db_session,
        workspace,
        agent,
        escalate_on_http_failure=False,
    )
    with (
        patch.object(
            flow_svc,
            "resolve_safe_url",
            return_value=("https://example.com/health", "93.184.216.34"),
        ),
        patch.object(
            flow_svc,
            "_execute_http",
            AsyncMock(return_value=(FlowRunOutcome.HTTP_ERROR, 503, None, 90)),
        ),
    ):
        run_id = await flow_svc._run_http_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.HTTP_ERROR
    assert run.status == FlowRunStatus.FAILED
    assert run.probe_response_status == 503


async def test_timeout_outcome(db_session, workspace, agent):
    flow = await _make_http_flow(db_session, workspace, agent)
    with (
        patch.object(
            flow_svc,
            "resolve_safe_url",
            return_value=("https://example.com/health", "93.184.216.34"),
        ),
        patch.object(
            flow_svc,
            "_execute_http",
            AsyncMock(return_value=(FlowRunOutcome.TIMEOUT, None, None, 30_000)),
        ),
    ):
        run_id = await flow_svc._run_http_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.TIMEOUT
    assert run.status == FlowRunStatus.FAILED


async def test_escalation_to_agent(db_session, workspace, agent):
    flow = await _make_http_flow(db_session, workspace, agent)
    with (
        patch.object(
            flow_svc,
            "resolve_safe_url",
            return_value=("https://example.com/health", "93.184.216.34"),
        ),
        patch.object(
            flow_svc,
            "_execute_http",
            AsyncMock(return_value=(FlowRunOutcome.ESCALATED_TO_AGENT, 503, None, 110)),
        ),
        patch.object(flow_svc, "_spawn_agent_run_task") as m_spawn,
    ):
        run_id = await flow_svc._run_http_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.ESCALATED_TO_AGENT
    assert run.status == FlowRunStatus.RUNNING
    assert run.probe_response_status == 503
    m_spawn.assert_called_once()


async def test_ssrf_blocked_short_circuits(db_session, workspace, agent):
    flow = await _make_http_flow(
        db_session,
        workspace,
        agent,
        http_url="http://localhost/admin",
    )

    def _raise(*_args, **_kwargs):
        raise UnsafeURLError("blocked", code="ssrf.blocked_hostname")

    with patch.object(flow_svc, "resolve_safe_url", side_effect=_raise):
        run_id = await flow_svc._run_http_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.SSRF_BLOCKED
    assert run.status == FlowRunStatus.FAILED


async def test_validation_failed_when_url_missing(db_session, workspace, agent):
    flow = await _make_http_flow(db_session, workspace, agent)
    flow.trigger_config = {}
    await db_session.flush()
    await db_session.commit()

    run_id = await flow_svc._run_http_flow(
        flow,
        trigger_kind=FlowTriggerKind.MANUAL,
        payload={},
        triggered_by=None,
    )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.VALIDATION_FAILED
    assert run.status == FlowRunStatus.FAILED
