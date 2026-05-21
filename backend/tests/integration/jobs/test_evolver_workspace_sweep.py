"""Integration: ``evolver_workspace_sweep`` ARQ task (M2.3).

Drives the daily cron task across multiple workspaces to assert:

* per-workspace failure isolation (one workspace's exception does not
  poison the rest of the sweep);
* the dispatcher is invoked exactly once per non-deleted workspace;
* the summary dict captures workspaces_seen / workspaces_skipped /
  workspaces_failed / proposals_created and one result entry per
  workspace.
"""

from __future__ import annotations

import uuid

import pytest

from app.jobs import evolver as job
from app.services import evolver_workflow as wf
from app.services.evolver_workflow import WorkflowExecutionResult

pytestmark = pytest.mark.asyncio


async def _bootstrap_workspace(async_client) -> str:
    email = f"ev-{uuid.uuid4().hex[:8]}@example.com"
    password = "evolver-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Evolver Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return workspace["id"]


async def test_sweep_invokes_dispatcher_per_workspace(async_client, monkeypatch):
    ws_a = uuid.UUID(await _bootstrap_workspace(async_client))
    ws_b = uuid.UUID(await _bootstrap_workspace(async_client))

    seen: list[uuid.UUID] = []

    async def _stub_dispatch(db, *, workspace_id, invocation_kind, actor_identity_id, bypass_min_artifacts):
        seen.append(workspace_id)
        return WorkflowExecutionResult(
            workspace_id=workspace_id,
            engine="workflow",
            artifacts_drained=0,
            artifacts_summarized=0,
            proposals_created=0,
            skipped=True,
            skip_reason="insufficient_artifacts",
            duration_ms=1,
            invocation_kind=invocation_kind,
        )

    monkeypatch.setattr(job, "evolve_workspace_skills", _stub_dispatch)

    summary = await job.evolver_workspace_sweep({})
    assert summary["status"] == "ok"
    assert summary["workspaces_seen"] >= 2
    assert ws_a in seen
    assert ws_b in seen
    # All stubs returned skipped=True so workspaces_skipped should match.
    assert summary["workspaces_skipped"] >= 2
    assert summary["proposals_created"] == 0
    assert isinstance(summary["results"], list)
    assert len(summary["results"]) == summary["workspaces_seen"]


async def test_sweep_isolates_per_workspace_failure(async_client, monkeypatch):
    ws_a = uuid.UUID(await _bootstrap_workspace(async_client))
    ws_b = uuid.UUID(await _bootstrap_workspace(async_client))

    async def _stub_dispatch(db, *, workspace_id, invocation_kind, actor_identity_id, bypass_min_artifacts):
        if workspace_id == ws_a:
            raise RuntimeError("simulated dispatcher crash")
        return WorkflowExecutionResult(
            workspace_id=workspace_id,
            engine="workflow",
            artifacts_drained=0,
            artifacts_summarized=0,
            proposals_created=2,
            skipped=False,
            skip_reason=None,
            duration_ms=42,
            invocation_kind=invocation_kind,
        )

    monkeypatch.setattr(job, "evolve_workspace_skills", _stub_dispatch)

    summary = await job.evolver_workspace_sweep({})
    assert summary["status"] == "ok"
    assert summary["workspaces_failed"] >= 1
    # Healthy workspace must still have its proposal counted.
    assert summary["proposals_created"] >= 2

    # Audit row for the failure path lands.
    from app.db.session import get_session_factory
    from sqlalchemy import select
    from app.db.models.audit import AuditEvent

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == ws_a,
                    AuditEvent.action == wf.AUDIT_FAILED,
                )
            )
        ).scalars().all()
    assert len(rows) >= 1
