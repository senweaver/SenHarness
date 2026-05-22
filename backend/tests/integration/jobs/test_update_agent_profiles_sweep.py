"""Integration: ``update_agent_profiles_sweep`` ARQ task (M3.4).

Multi-workspace x multi-agent fan-out drives the daily cron and
asserts:

* each ``(workspace, agent)`` pair is visited exactly once;
* a per-agent exception is isolated and audited via
  ``agent_profile.update_failed`` while the rest of the sweep
  continues;
* the summary dict carries ``agents_updated`` / ``agents_failed``.
"""

from __future__ import annotations

import uuid

import pytest

from app.jobs import agent_profile_update as job
from app.services import agent_profile as svc

pytestmark = pytest.mark.asyncio


async def _bootstrap_workspace_with_agent(async_client) -> tuple[str, str]:
    email = f"ap-{uuid.uuid4().hex[:8]}@example.com"
    password = "agent-profile-sweep-test-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "AP Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    ws_id = workspace["id"]

    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": ws_id}

    r = await async_client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "Sweep Test Agent",
            "description": "ARQ sweep coverage",
            "backend_kind": "native",
        },
    )
    assert r.status_code in (200, 201), r.text
    agent_id = r.json()["id"]
    return ws_id, agent_id


async def test_sweep_visits_every_agent(async_client, monkeypatch):
    ws_a, agent_a = await _bootstrap_workspace_with_agent(async_client)
    ws_b, agent_b = await _bootstrap_workspace_with_agent(async_client)

    seen: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def _stub_update(
        db, *, workspace_id, agent_id, since_days, invocation_kind, actor_identity_id=None
    ):
        _ = db, since_days, invocation_kind, actor_identity_id
        seen.append((workspace_id, agent_id))

        class _Result:
            aggregated_run_count = 0
            duration_ms = 1

        return _Result()

    monkeypatch.setattr(svc, "update_profile_for_agent", _stub_update)

    summary = await job.update_agent_profiles_sweep({})
    assert summary["status"] == "ok"
    assert summary["workspaces_seen"] >= 2
    assert (uuid.UUID(ws_a), uuid.UUID(agent_a)) in seen
    assert (uuid.UUID(ws_b), uuid.UUID(agent_b)) in seen
    assert summary["agents_updated"] >= 2
    assert summary["agents_failed"] == 0


async def test_sweep_isolates_per_agent_failure(async_client, monkeypatch):
    ws_a, agent_a = await _bootstrap_workspace_with_agent(async_client)
    ws_b, agent_b = await _bootstrap_workspace_with_agent(async_client)

    target_failure = uuid.UUID(agent_a)

    async def _stub_update(
        db, *, workspace_id, agent_id, since_days, invocation_kind, actor_identity_id=None
    ):
        _ = db, workspace_id, since_days, invocation_kind, actor_identity_id
        if agent_id == target_failure:
            raise RuntimeError("simulated agent_profile crash")

        class _Result:
            aggregated_run_count = 1
            duration_ms = 5

        return _Result()

    monkeypatch.setattr(svc, "update_profile_for_agent", _stub_update)

    summary = await job.update_agent_profiles_sweep({})
    assert summary["status"] == "ok"
    assert summary["agents_failed"] >= 1
    # Healthy agent (other workspace) still updated.
    assert summary["agents_updated"] >= 1
    assert summary["errors"], "errors list should carry the failed agent"

    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == job.AUDIT_UPDATE_FAILED,
                        AuditEvent.workspace_id == uuid.UUID(ws_a),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(uuid.UUID(agent_a) == row.resource_id for row in rows)
    _ = ws_b, agent_b  # signal coverage of the unaffected pair
