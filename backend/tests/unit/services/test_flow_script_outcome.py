"""Coverage for ``_run_script_flow`` outcomes.

We mock ``_execute_script`` so the assertions are about how the
outcome is mapped onto FlowRun rows, audit events, and the optional
escalation bridge — not about the sandbox itself, which has its own
test suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.db.models.flow import (
    FlowExecutionMode,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.services import flow as flow_svc


async def _make_script_flow(db_session, workspace, agent, **cfg):
    repo = FlowRepository(db_session)
    flow = await repo.create(
        workspace_id=workspace.id,
        name="script-flow",
        description=None,
        trigger_kind=FlowTriggerKind.MANUAL,
        trigger_config={"script_command": "echo hi", **cfg},
        execution_mode=FlowExecutionMode.NO_AGENT_SCRIPT,
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


async def test_silent_success_when_stdout_empty(db_session, workspace, agent):
    flow = await _make_script_flow(db_session, workspace, agent)
    with patch.object(
        flow_svc,
        "_execute_script",
        AsyncMock(return_value=(FlowRunOutcome.SUCCESS, 0, None, 12)),
    ):
        run_id = await flow_svc._run_script_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run is not None
    assert run.outcome == FlowRunOutcome.SUCCESS
    assert run.status == FlowRunStatus.SUCCEEDED
    assert run.error is None
    assert run.probe_duration_ms == 12


async def test_nonempty_output_no_escalation(db_session, workspace, agent):
    flow = await _make_script_flow(
        db_session,
        workspace,
        agent,
        escalate_on_nonempty_output=False,
    )
    with patch.object(
        flow_svc,
        "_execute_script",
        AsyncMock(return_value=(FlowRunOutcome.NONEMPTY_OUTPUT, 0, "hello\n", 30)),
    ):
        run_id = await flow_svc._run_script_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.NONEMPTY_OUTPUT
    assert run.status == FlowRunStatus.SUCCEEDED
    assert run.probe_output_excerpt == "hello\n"


async def test_script_error_records_exit_code(db_session, workspace, agent):
    flow = await _make_script_flow(db_session, workspace, agent)
    with patch.object(
        flow_svc,
        "_execute_script",
        AsyncMock(return_value=(FlowRunOutcome.SCRIPT_ERROR, 7, "boom", 50)),
    ):
        run_id = await flow_svc._run_script_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.SCRIPT_ERROR
    assert run.status == FlowRunStatus.FAILED
    assert "exit_code=7" in (run.error or "")


async def test_timeout_records_outcome(db_session, workspace, agent):
    flow = await _make_script_flow(db_session, workspace, agent)
    with patch.object(
        flow_svc,
        "_execute_script",
        AsyncMock(return_value=(FlowRunOutcome.TIMEOUT, None, None, 60_000)),
    ):
        run_id = await flow_svc._run_script_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.TIMEOUT
    assert run.status == FlowRunStatus.FAILED
    assert "timeout" in (run.error or "")


async def test_escalation_creates_running_row(db_session, workspace, agent):
    flow = await _make_script_flow(db_session, workspace, agent)
    with (
        patch.object(
            flow_svc,
            "_execute_script",
            AsyncMock(
                return_value=(
                    FlowRunOutcome.ESCALATED_TO_AGENT,
                    0,
                    "details",
                    44,
                )
            ),
        ),
        patch.object(flow_svc, "_spawn_agent_run_task") as m_spawn,
    ):
        run_id = await flow_svc._run_script_flow(
            flow,
            trigger_kind=FlowTriggerKind.MANUAL,
            payload={"k": "v"},
            triggered_by=None,
        )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.ESCALATED_TO_AGENT
    assert run.status == FlowRunStatus.RUNNING
    assert run.probe_output_excerpt == "details"
    m_spawn.assert_called_once()


async def test_validation_failed_for_missing_command(db_session, workspace, agent):
    flow = await _make_script_flow(db_session, workspace, agent)
    flow.trigger_config = {}
    await db_session.flush()
    await db_session.commit()
    run_id = await flow_svc._run_script_flow(
        flow,
        trigger_kind=FlowTriggerKind.MANUAL,
        payload={},
        triggered_by=None,
    )
    await db_session.expire_all()
    run = await _read_run(db_session, run_id)
    assert run.outcome == FlowRunOutcome.VALIDATION_FAILED
    assert run.status == FlowRunStatus.FAILED
