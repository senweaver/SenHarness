"""Unit tests for the M2.5.6 ``max_nesting_depth`` gate.

The gate is the recursion budget that lets a top-level run spawn a
small layered tree of children without spine-table blowup. Defaults
land at depth 3 — a parent can fan out, each child can fan out once
more, and a third layer is rejected.

These cases drive the gate at boundary depths so the rejection path
audit + result envelope shape stay locked. ``_run_single_child`` is
stubbed so the test never touches the model resolver.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.harness import subagents as subagents_svc
from app.services.subagent_batch_config import ResolvedSubagentBatchConfig

pytestmark = pytest.mark.asyncio


def _make_tasks(n: int) -> list[subagents_svc.SubAgentTask]:
    return [
        subagents_svc.SubAgentTask(
            task_id=f"t-{i}",
            prompt=f"do {i}",
            target_agent_id=uuid.uuid4(),
        )
        for i in range(n)
    ]


async def test_spawn_depth_equal_to_cap_is_rejected(monkeypatch):
    """spawn_depth=3 with max_nesting_depth=3 → all tasks rejected."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_events: list[tuple[str, dict]] = []

    async def fake_audit(**kwargs):
        audit_events.append((kwargs["action"], kwargs.get("metadata") or {}))

    async def fake_child(**kwargs):
        # Should NEVER be called when the gate trips.
        raise AssertionError("child should not run when depth gate trips")

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", fake_child)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=_make_tasks(2),
        spawn_depth=3,
    )

    assert result.total == 2
    assert result.rejected == 2
    assert result.completed == 0
    assert all(r.status == "rejected" for r in result.results.values())
    assert all(
        r.error_kind == "nesting_depth_exceeded"
        for r in result.results.values()
    )
    actions = [a for a, _ in audit_events]
    assert subagents_svc.AUDIT_NESTING_DEPTH_EXCEEDED in actions
    assert subagents_svc.AUDIT_BATCH_STARTED not in actions
    assert subagents_svc.AUDIT_BATCH_COMPLETED not in actions

    metadata = next(
        m for a, m in audit_events
        if a == subagents_svc.AUDIT_NESTING_DEPTH_EXCEEDED
    )
    assert metadata["spawn_depth"] == 3
    assert metadata["max_nesting_depth"] == 3
    assert metadata["task_count"] == 2


async def test_spawn_depth_exceeds_cap_is_rejected(monkeypatch):
    """spawn_depth=5 vs cap=3 still rejects with the same audit."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_actions: list[str] = []

    async def fake_audit(**kwargs):
        audit_actions.append(kwargs["action"])

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=_make_tasks(1),
        spawn_depth=5,
    )

    assert result.rejected == 1
    assert subagents_svc.AUDIT_NESTING_DEPTH_EXCEEDED in audit_actions


async def test_spawn_depth_below_cap_executes(monkeypatch):
    """spawn_depth=2 vs cap=3 → tasks dispatch normally, no rejection audit."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_actions: list[str] = []

    async def fake_audit(**kwargs):
        audit_actions.append(kwargs["action"])

    async def fake_child(*, task, **_):
        return subagents_svc.SubAgentResult(
            task_id=task.task_id,
            child_run_id=uuid.uuid4(),
            status="completed",
        )

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", fake_child)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=_make_tasks(2),
        spawn_depth=2,
    )

    assert result.total == 2
    assert result.completed == 2
    assert result.rejected == 0
    assert subagents_svc.AUDIT_NESTING_DEPTH_EXCEEDED not in audit_actions
    assert subagents_svc.AUDIT_BATCH_COMPLETED in audit_actions


async def test_max_nesting_depth_one_blocks_all_recursion(monkeypatch):
    """A workspace that disables nested batching (cap=1) rejects depth>=1."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=1,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_actions: list[str] = []

    async def fake_audit(**kwargs):
        audit_actions.append(kwargs["action"])

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)

    # spawn_depth=1 against cap=1 → reject (>= edge).
    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=_make_tasks(2),
        spawn_depth=1,
    )
    assert result.rejected == 2
    assert subagents_svc.AUDIT_NESTING_DEPTH_EXCEEDED in audit_actions
