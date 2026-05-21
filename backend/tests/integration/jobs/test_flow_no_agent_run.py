"""End-to-end: triggering a no-agent flow writes a FlowRun + audit row.

Sandbox / httpx are mocked so we exercise persistence, audit fan-out,
and outcome assignment — but not the underlying execution machinery,
which has its own tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.flow import (
    FlowExecutionMode,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.db.session import get_session_factory
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.services import flow as flow_svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"flowe2e-{uuid.uuid4().hex[:8]}@example.com"
    password = "flow-e2e-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Flow E2E", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "FlowE2E WS", "slug": f"flow-e2e-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id

    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "Flow Bot", "persona_md": "you are a bot"},
    )
    assert r.status_code in (200, 201), r.text
    agent_id = r.json()["id"]

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "e2e-script",
            "execution_mode": FlowExecutionMode.NO_AGENT_SCRIPT.value,
            "trigger_config": {"script_command": "echo silent"},
            "agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    flow_id = r.json()["id"]
    return {"flow_id": flow_id, "ws_id": ws_id, "headers": headers}, ws_id


async def test_no_agent_script_e2e(async_client):
    info, ws_id = await _bootstrap(async_client)
    flow_id_str = info["flow_id"]

    factory = get_session_factory()
    async with factory() as db:
        flow = await FlowRepository(db).get(uuid.UUID(flow_id_str))
        assert flow is not None
        assert flow.execution_mode == FlowExecutionMode.NO_AGENT_SCRIPT

    with patch.object(
        flow_svc,
        "_execute_script",
        AsyncMock(return_value=(FlowRunOutcome.SUCCESS, 0, None, 12)),
    ):
        run_id = await flow_svc.trigger_flow(
            uuid.UUID(flow_id_str),
            workspace_id=uuid.UUID(ws_id),
            trigger_kind=FlowTriggerKind.MANUAL,
        )

    factory = get_session_factory()
    async with factory() as db:
        run = await FlowRunRepository(db).get(run_id)
        assert run is not None
        assert run.outcome == FlowRunOutcome.SUCCESS
        assert run.status == FlowRunStatus.SUCCEEDED
        assert run.probe_duration_ms == 12

        events = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == uuid.UUID(ws_id))
                .where(AuditEvent.action == "flow.script_executed")
            )
        ).scalars().all()
        assert len(events) >= 1
        meta = events[0].metadata_json or {}
        assert meta.get("outcome") == "success"
        assert meta.get("flow_id") == flow_id_str
