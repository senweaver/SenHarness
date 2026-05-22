"""Integration: per-child hallucination gate inside a batch (M2.5.6).

Confirms the design point that the M2.5.1 gate runs **per child
independently** — a slow / failing aux call on one sibling cannot
block the others, and a low-score child gets parked in
``HALLUCINATION_REVIEW`` with its own pending Approval row while the
high-score sibling completes normally.

The test stubs the aux LLM at the ``evaluate_hallucination`` boundary
so no real model is touched; the breaker check is forced open=False so
the path goes through the actual gate (not the fail-open shortcut).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.agents.harness import subagents as subagents_svc
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.subagent_run import SubAgentRunState
from app.repositories.subagent_run import SubAgentRunRepository
from app.services import subagent_run as subagent_svc
from app.services.subagent_batch_config import ResolvedSubagentBatchConfig

pytestmark = pytest.mark.asyncio


def _share_session_factory(monkeypatch, db_session) -> None:
    lock = asyncio.Lock()

    @asynccontextmanager
    async def _ctx():
        async with lock:
            yield db_session

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _ctx)


async def test_one_child_hallucinates_others_complete(db_session, workspace, monkeypatch):
    """3 children: one returns text the aux scores 0.20 → halluc_review;
    two return text scoring 0.80 → completed. Tally + spine rows
    reflect the split."""
    _share_session_factory(monkeypatch, db_session)

    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=3,
        max_nesting_depth=3,
    )

    async def _load(*, workspace_id):
        return config

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", _load)

    parent_run_id = uuid.uuid4()
    workspace_id = workspace.id

    halluc_task = "task-haunted"

    class _GoodAgent:
        async def run(self, prompt: str):
            await asyncio.sleep(0.005)

            class _Out:
                output = "Tool: web_search returned 2 hits; the answer is 42."

            return _Out()

    class _HallucAgent:
        async def run(self, prompt: str):
            await asyncio.sleep(0.005)

            class _Out:
                output = "I think probably maybe the answer is 17 — vibes."

            return _Out()

    halluc_target_ids: set[uuid.UUID] = set()

    async def _fake_resolve(*, workspace_id, target_agent_id):
        return target_agent_id, None

    monkeypatch.setattr(subagents_svc, "_resolve_child_agent_model", _fake_resolve)

    def _build(*, model, persona_md):
        if model in halluc_target_ids:
            return _HallucAgent()
        return _GoodAgent()

    monkeypatch.setattr(subagents_svc, "_build_child_agent", _build)

    # Per-child aux scoring: 0.20 for the haunted task, 0.80 for the
    # well-grounded ones. The gate threshold default is 0.50.
    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        if "vibes" in (final_output or "").lower():
            return 0.20, "stub:aux"
        return 0.80, "stub:aux"

    async def fake_breaker_open(*, bucket, workspace_id, trip_at):
        return False

    monkeypatch.setattr(subagent_svc, "evaluate_hallucination", fake_evaluate)
    monkeypatch.setattr("app.jobs._breaker.is_breaker_open", fake_breaker_open)

    halluc_target = uuid.uuid4()
    halluc_target_ids.add(halluc_target)
    tasks = [
        subagents_svc.SubAgentTask(
            task_id=f"task-good-{i}",
            prompt="p",
            target_agent_id=uuid.uuid4(),
        )
        for i in range(2)
    ] + [
        subagents_svc.SubAgentTask(
            task_id=halluc_task,
            prompt="p",
            target_agent_id=halluc_target,
        )
    ]

    summary = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=tasks,
        max_concurrent=3,
    )

    assert summary.total == 3
    assert summary.completed == 2
    assert summary.halluc_review == 1

    haunted = summary.results[halluc_task]
    assert haunted.status == "halluc_review"

    # Spine row for the halluc child should be in HALLUCINATION_REVIEW
    # with a pending Approval attached.
    repo = SubAgentRunRepository(db_session)
    row = await repo.get_by_child_run_id(child_run_id=haunted.child_run_id)
    assert row is not None
    assert row.state == SubAgentRunState.HALLUCINATION_REVIEW
    approval_id = row.hallucination_approval_id
    assert approval_id is not None

    approval = (
        await db_session.execute(select(Approval).where(Approval.id == approval_id))
    ).scalar_one_or_none()
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == subagent_svc.HALLUCINATION_RESOURCE_TYPE

    # Sibling spine rows should be COMPLETED — independent gate.
    for task_id in (f"task-good-{i}" for i in range(2)):
        good = summary.results[task_id]
        good_row = await repo.get_by_child_run_id(child_run_id=good.child_run_id)
        assert good_row is not None
        assert good_row.state == SubAgentRunState.COMPLETED
