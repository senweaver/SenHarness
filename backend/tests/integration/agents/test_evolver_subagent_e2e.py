"""End-to-end tests for :func:`invoke_evolver_subagent` (M2.2).

The aux model is mocked at the pydantic-ai layer via ``TestModel`` so
no real LLM provider is touched. We script three behaviours:

1. The mocked model issues a single ``propose_skill_create`` tool
   call → an Approval row + audit row land in Postgres, the result
   reports ``proposals_created == 1``.
2. The mocked model calls ``mark_skip`` directly →
   ``EvolverInvokeResult.skipped`` is true, no proposals filed, the
   ``evolver.marked_skip`` audit row is written.
3. The mocked model never returns (forces the timeout path) → the
   timeout audit lands, the breaker counter advances, the result
   carries ``timed_out=True``.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.agents.builtin import evolver_agent as ev
from app.agents.builtin.evolver_agent import (
    AUDIT_INVOKED,
    AUDIT_SUBAGENT_COMPLETED,
    AUDIT_TIMEOUT,
    EVOLVER_BREAKER_BUCKET,
    invoke_evolver_subagent,
)
from app.db.models.approval import Approval, ApprovalResourceType
from app.db.models.audit import AuditEvent

pytestmark = pytest.mark.asyncio


# ─── Helpers ─────────────────────────────────────────────────
async def _enable(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


def _factory(db_session):
    @asynccontextmanager
    async def _f():
        yield db_session

    return _f


def _make_test_model_with_calls(call_specs):
    """Build a pydantic-ai TestModel that issues a fixed sequence of
    tool calls then a final text answer. ``call_specs`` is
    ``[(tool_name, args_dict), ...]``; pass ``[]`` for a model that
    just answers without tools.
    """
    from pydantic_ai.models.test import TestModel

    return TestModel(call_tools=[name for name, _ in call_specs] if call_specs else [])


@pytest.fixture
def patch_factory(monkeypatch, db_session):
    """Route every short-lived session opened by the agent module
    through the same per-test ``db_session`` so tests can assert on
    rows the agent inserted.
    """
    monkeypatch.setattr(ev, "get_session_factory", lambda: _factory(db_session))
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _factory(db_session),
    )
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _factory(db_session),
    )
    yield


# ─── Test 1: single propose_skill_create ─────────────────────
async def test_single_propose_create_lands_approval(
    db_session, workspace, identity, monkeypatch, patch_factory
):
    await _enable(db_session, workspace)

    # Stub aux resolution → return a TestModel that immediately
    # issues a ``propose_skill_create`` tool call. The TestModel's
    # default behaviour is to call every tool with model-generated
    # args once, so we restrict it to the verb we want.
    from pydantic_ai.models.test import TestModel

    test_model = TestModel(call_tools=["propose_skill_create"])

    async def _stub_resolve(**_kwargs):
        from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask

        return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="test:test")

    monkeypatch.setattr(ev, "_resolve_aux_config", _stub_resolve)
    monkeypatch.setattr(ev, "_build_pydantic_ai_model_from_config", lambda _cfg: test_model)
    # The breaker check uses Redis; in the unit-friendly env it
    # silently fails open so we don't need to stub it.

    result = await invoke_evolver_subagent(
        workspace_id=workspace.id,
        triggering_run_ids=[uuid.uuid4()],
        invocation_kind="manual",
        actor_identity_id=identity.id,
    )
    assert result.timed_out is False
    assert result.error is None
    assert result.proposals_created >= 1
    assert result.skipped is False

    approvals = list(
        (
            await db_session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace.id,
                    Approval.run_id == result.run_id,
                )
            )
        ).scalars()
    )
    assert len(approvals) >= 1
    assert approvals[0].resource_type == ApprovalResourceType.SKILL_PACK_CREATE.value

    invoked = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_INVOKED,
                )
            )
        ).scalars()
    )
    assert len(invoked) == 1

    completed = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_SUBAGENT_COMPLETED,
                )
            )
        ).scalars()
    )
    assert len(completed) == 1
    assert completed[0].metadata_json["proposals_created"] >= 1


# ─── Test 2: mark_skip path ──────────────────────────────────
async def test_mark_skip_path_no_proposals(
    db_session, workspace, identity, monkeypatch, patch_factory
):
    await _enable(db_session, workspace)

    from pydantic_ai.models.test import TestModel

    test_model = TestModel(
        call_tools=["mark_skip"],
        custom_output_text=("No SkillPack proposals worth filing. The batch looks healthy."),
    )

    async def _stub_resolve(**_kwargs):
        from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask

        return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="test:test")

    monkeypatch.setattr(ev, "_resolve_aux_config", _stub_resolve)
    monkeypatch.setattr(ev, "_build_pydantic_ai_model_from_config", lambda _cfg: test_model)

    result = await invoke_evolver_subagent(
        workspace_id=workspace.id,
        invocation_kind="manual",
        actor_identity_id=identity.id,
    )
    assert result.skipped is True
    assert result.proposals_created == 0
    assert result.timed_out is False
    assert result.error is None

    approvals = list(
        (
            await db_session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace.id,
                    Approval.run_id == result.run_id,
                )
            )
        ).scalars()
    )
    assert approvals == []

    skip_audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "evolver.marked_skip",
                )
            )
        ).scalars()
    )
    assert len(skip_audits) == 1


# ─── Test 3: timeout path ────────────────────────────────────
async def test_timeout_path_audits_and_bumps_breaker(
    db_session, workspace, identity, monkeypatch, patch_factory
):
    await _enable(db_session, workspace)

    class _NeverReturnsModel:
        async def request(self, *_args, **_kwargs):
            await asyncio.sleep(60)
            return None

        async def request_stream(self, *_args, **_kwargs):
            await asyncio.sleep(60)
            return None

    async def _stub_resolve(**_kwargs):
        from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask

        return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="test:hangs")

    monkeypatch.setattr(ev, "_resolve_aux_config", _stub_resolve)

    # Use a hanging stub at the agent.run level so we don't depend on
    # pydantic-ai internals for this path. We replace the whole
    # ``build_evolver_agent`` to return an object whose ``run``
    # never resolves.
    class _HangingAgent:
        async def run(self, _prompt):
            await asyncio.sleep(60)

    monkeypatch.setattr(ev, "_build_pydantic_ai_model_from_config", lambda _cfg: object())
    monkeypatch.setattr(ev, "build_evolver_agent", lambda *, model: _HangingAgent())

    bump_calls: list[dict] = []

    async def _bump(**kwargs):
        bump_calls.append(kwargs)
        return 1

    monkeypatch.setattr(ev, "bump_failure", _bump)

    result = await invoke_evolver_subagent(
        workspace_id=workspace.id,
        invocation_kind="scheduled",
        actor_identity_id=identity.id,
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.error is not None
    assert "exceeded" in result.error
    assert result.proposals_created == 0

    timeout_audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_TIMEOUT,
                )
            )
        ).scalars()
    )
    assert len(timeout_audits) == 1

    assert bump_calls, "bump_failure should be invoked on timeout"
    assert bump_calls[0]["bucket"] == EVOLVER_BREAKER_BUCKET
    assert bump_calls[0]["workspace_id"] == str(workspace.id)
