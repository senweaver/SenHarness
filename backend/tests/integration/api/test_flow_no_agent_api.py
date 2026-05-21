"""M0.6 — REST coverage for the lightweight flow modes.

Exercises the schema-level 422, the test-script / test-http dry-run
endpoints, and a representative RBAC failure. Sandbox + httpx are
mocked at the service boundary so the test stays hermetic.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models.flow import FlowExecutionMode, FlowRunOutcome

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"flow-{uuid.uuid4().hex[:8]}@example.com"
    password = "flow-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Flow Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    identity_id = r.json()["id"]
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Flow WS", "slug": f"flow-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id, identity_id


async def _create_agent(async_client, headers) -> str:
    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "Flow Bot", "persona_md": "you are a bot"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_create_script_flow_missing_command_returns_422(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "broken-script",
            "execution_mode": FlowExecutionMode.NO_AGENT_SCRIPT.value,
            "trigger_config": {},
            "agent_id": agent_id,
        },
    )
    assert r.status_code == 422, r.text


async def test_create_http_flow_missing_url_returns_422(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "broken-http",
            "execution_mode": FlowExecutionMode.NO_AGENT_HTTP.value,
            "trigger_config": {},
            "agent_id": agent_id,
        },
    )
    assert r.status_code == 422, r.text


async def test_create_script_flow_happy_path(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "good-script",
            "execution_mode": FlowExecutionMode.NO_AGENT_SCRIPT.value,
            "trigger_config": {
                "script_command": "echo hi",
                "script_timeout_s": 10,
            },
            "agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["execution_mode"] == "no_agent_script"
    assert body["trigger_config"]["script_command"] == "echo hi"


async def test_test_script_dry_run(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "dry-script",
            "execution_mode": FlowExecutionMode.NO_AGENT_SCRIPT.value,
            "trigger_config": {"script_command": "echo hi"},
            "agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    flow_id = r.json()["id"]

    fake = {
        "outcome": FlowRunOutcome.SUCCESS,
        "duration_ms": 12,
        "exit_code": 0,
        "output_excerpt": None,
        "error": None,
    }
    with patch(
        "app.services.flow.dry_run_script",
        AsyncMock(return_value=fake),
    ):
        r = await async_client.post(
            f"/api/v1/flows/{flow_id}/test-script",
            headers=headers,
            json=None,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "success"
    assert body["duration_ms"] == 12


async def test_test_http_dry_run(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "dry-http",
            "execution_mode": FlowExecutionMode.NO_AGENT_HTTP.value,
            "trigger_config": {"http_url": "https://example.com/health"},
            "agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    flow_id = r.json()["id"]

    fake = {
        "outcome": FlowRunOutcome.SILENT_2XX,
        "duration_ms": 70,
        "response_status": 200,
        "error": None,
    }
    with patch(
        "app.services.flow.dry_run_http",
        AsyncMock(return_value=fake),
    ):
        r = await async_client.post(
            f"/api/v1/flows/{flow_id}/test-http",
            headers=headers,
            json=None,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "silent_2xx"
    assert body["response_status"] == 200


async def test_test_endpoints_require_admin(async_client):
    headers, _ws, _ident = await _bootstrap(async_client)
    agent_id = await _create_agent(async_client, headers)

    r = await async_client.post(
        "/api/v1/flows",
        headers=headers,
        json={
            "name": "admin-guard",
            "execution_mode": FlowExecutionMode.NO_AGENT_SCRIPT.value,
            "trigger_config": {"script_command": "echo hi"},
            "agent_id": agent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    flow_id = r.json()["id"]

    # Owner of the workspace IS an admin so the happy-path admin call
    # should still pass; but invite a fresh second identity that is NOT a
    # member and verify they get 401/403.
    other_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
    other_pw = "other-pass-very-long"
    r2 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "name": "Other", "password": other_pw},
    )
    assert r2.status_code == 201, r2.text
    r3 = await async_client.post(
        "/api/v1/auth/login",
        json={"email": other_email, "password": other_pw},
    )
    other_headers = {
        "Authorization": f"Bearer {r3.json()['access_token']}",
        "X-Workspace-Id": headers["X-Workspace-Id"],
    }

    r4 = await async_client.post(
        f"/api/v1/flows/{flow_id}/test-script",
        headers=other_headers,
        json=None,
    )
    assert r4.status_code in (401, 403, 404), r4.text
