"""Unit: ``evolver_workflow.evolve_workspace_skills`` dispatcher (M2.3).

The dispatcher reads ``EvolverSettings.engine`` from the resolved
workspace config and routes to one of two implementations. These
cases stub the engine impls so they assert *only* the routing
contract — not the engine bodies (those have their own tests).
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.platform_settings import EvolverSettings
from app.services import evolver_workflow as svc
from app.services.evolver_workflow import WorkflowExecutionResult

pytestmark = pytest.mark.asyncio


def _make_result(engine: str) -> WorkflowExecutionResult:
    return WorkflowExecutionResult(
        workspace_id=uuid.uuid4(),
        engine=engine,  # type: ignore[arg-type]
        artifacts_drained=0,
        artifacts_summarized=0,
        proposals_created=0,
        skipped=False,
        skip_reason=None,
        duration_ms=1,
    )


async def test_dispatcher_routes_to_workflow_engine(monkeypatch):
    workspace_id = uuid.uuid4()
    seen: list[str] = []

    async def _stub_get_config(db, *, workspace_id):  # type: ignore[no-untyped-def]
        return EvolverSettings(enabled=True, engine="workflow")

    async def _stub_workflow(db, **kwargs):  # type: ignore[no-untyped-def]
        seen.append("workflow")
        return _make_result("workflow")

    async def _stub_agent(db, **kwargs):  # type: ignore[no-untyped-def]
        seen.append("agent")
        return _make_result("agent")

    monkeypatch.setattr(svc, "get_workspace_evolver_config", _stub_get_config)
    monkeypatch.setattr(svc, "evolve_workspace_skills_workflow", _stub_workflow)
    monkeypatch.setattr(svc, "evolve_workspace_skills_agent", _stub_agent)

    result = await svc.evolve_workspace_skills(
        db=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        invocation_kind="manual",
        actor_identity_id=None,
    )
    assert result.engine == "workflow"
    assert seen == ["workflow"]


async def test_dispatcher_routes_to_agent_engine(monkeypatch):
    workspace_id = uuid.uuid4()
    seen: list[str] = []

    async def _stub_get_config(db, *, workspace_id):  # type: ignore[no-untyped-def]
        return EvolverSettings(enabled=True, engine="agent")

    async def _stub_workflow(db, **kwargs):  # type: ignore[no-untyped-def]
        seen.append("workflow")
        return _make_result("workflow")

    async def _stub_agent(db, **kwargs):  # type: ignore[no-untyped-def]
        seen.append("agent")
        return _make_result("agent")

    monkeypatch.setattr(svc, "get_workspace_evolver_config", _stub_get_config)
    monkeypatch.setattr(svc, "evolve_workspace_skills_workflow", _stub_workflow)
    monkeypatch.setattr(svc, "evolve_workspace_skills_agent", _stub_agent)

    result = await svc.evolve_workspace_skills(
        db=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        invocation_kind="scheduled",
    )
    assert result.engine == "agent"
    assert seen == ["agent"]


async def test_workflow_disabled_workspace_returns_skip(monkeypatch):
    """When EvolverSettings.enabled is False the workflow short-circuits.

    The skip decision lives inside the engine impls (via _preflight),
    not in the dispatcher — but the contract is the same: caller sees
    ``skipped=True`` and ``skip_reason='evolver_disabled'``.
    """
    workspace_id = uuid.uuid4()
    audit_actions: list[str] = []

    async def _stub_get_config(db, *, workspace_id):  # type: ignore[no-untyped-def]
        return EvolverSettings(enabled=False, engine="workflow")

    async def _stub_breaker(*, bucket, workspace_id, trip_at):  # type: ignore[no-untyped-def]
        return False

    async def _stub_audit(**kwargs):  # type: ignore[no-untyped-def]
        audit_actions.append(kwargs.get("action") or "")

    monkeypatch.setattr(svc, "get_workspace_evolver_config", _stub_get_config)
    monkeypatch.setattr(svc, "is_breaker_open", _stub_breaker)
    monkeypatch.setattr(svc, "_record_audit", _stub_audit)

    result = await svc.evolve_workspace_skills_workflow(
        db=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        invocation_kind="scheduled",
    )
    assert result.skipped is True
    assert result.skip_reason == "evolver_disabled"
    assert result.proposals_created == 0
    assert svc.AUDIT_SKIPPED in audit_actions


async def test_workflow_breaker_open_returns_skip(monkeypatch):
    """Tripped breaker short-circuits without touching the LLM stack."""
    workspace_id = uuid.uuid4()
    audit_actions: list[str] = []

    async def _stub_get_config(db, *, workspace_id):  # type: ignore[no-untyped-def]
        return EvolverSettings(enabled=True, engine="workflow")

    async def _stub_breaker(*, bucket, workspace_id, trip_at):  # type: ignore[no-untyped-def]
        return True  # breaker open

    async def _stub_audit(**kwargs):  # type: ignore[no-untyped-def]
        audit_actions.append(kwargs.get("action") or "")

    monkeypatch.setattr(svc, "get_workspace_evolver_config", _stub_get_config)
    monkeypatch.setattr(svc, "is_breaker_open", _stub_breaker)
    monkeypatch.setattr(svc, "_record_audit", _stub_audit)

    result = await svc.evolve_workspace_skills_workflow(
        db=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        invocation_kind="scheduled",
    )
    assert result.skipped is True
    assert result.skip_reason == "breaker_open"
    assert svc.AUDIT_SKIPPED in audit_actions
