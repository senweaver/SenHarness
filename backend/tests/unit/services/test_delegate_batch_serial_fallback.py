"""Unit tests for the M2.5.6 batch-spawn serial-fallback path.

Three coverage points:

* ``max_batch_size=1`` workspace policy → serial (single child at a
  time even when caller requested multiple), and the audit chain
  carries ``serial_fallback_reason='max_batch_size_one'``.
* ``batch_enabled=False`` → serial regardless of caller args, audit
  reason ``batch_disabled``.
* Single-task batches always fall back to serial silently (a 1-task
  batch with concurrency budget 5 is wasteful), audit reason
  ``single_task``.

Each case stubs ``_run_single_child`` so the test stays under the
heartbeat path — we only verify the batch dispatch shape, not the
child agent lifecycle (M2.5.1 owns those).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

from app.agents.harness import subagents as subagents_svc
from app.services.subagent_batch_config import ResolvedSubagentBatchConfig

pytestmark = pytest.mark.asyncio


def _make_task(idx: int) -> subagents_svc.SubAgentTask:
    return subagents_svc.SubAgentTask(
        task_id=f"task-{idx}",
        prompt=f"do task {idx}",
        target_agent_id=uuid.uuid4(),
    )


async def _stub_child(
    *, task: subagents_svc.SubAgentTask, **_: Any
) -> subagents_svc.SubAgentResult:
    return subagents_svc.SubAgentResult(
        task_id=task.task_id,
        child_run_id=uuid.uuid4(),
        status="completed",
        output=f"ok:{task.task_id}",
        duration_ms=1,
    )


async def test_max_batch_size_one_falls_back_to_serial(monkeypatch):
    """workspace policy with max_batch_size=1 forces serial loop."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=1,
        max_concurrent=5,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_events: list[tuple[str, dict]] = []

    async def fake_audit(**kwargs):
        audit_events.append((kwargs["action"], kwargs.get("metadata") or {}))

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", _stub_child)

    workspace_id = uuid.uuid4()
    parent_run_id = uuid.uuid4()

    result = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[_make_task(0), _make_task(1), _make_task(2)],
    )

    # Over-quota tasks land as ``rejected`` (not silently dropped).
    assert result.total == 3
    assert result.completed == 1
    assert result.rejected == 2
    assert result.serial_fallback is True
    assert result.serial_fallback_reason == "max_batch_size_one"
    assert result.max_concurrent_used == 1

    actions = [a for a, _ in audit_events]
    assert subagents_svc.AUDIT_BATCH_SERIAL_FALLBACK in actions
    assert subagents_svc.AUDIT_BATCH_STARTED in actions
    assert subagents_svc.AUDIT_BATCH_COMPLETED in actions

    started_meta = next(m for a, m in audit_events if a == subagents_svc.AUDIT_BATCH_STARTED)
    assert started_meta["serial_fallback"] is True
    assert started_meta["serial_fallback_reason"] == "max_batch_size_one"
    assert started_meta["max_concurrent"] == 1


async def test_batch_disabled_falls_back_to_serial(monkeypatch):
    """workspace.subagent.batch_enabled=False → serial loop, reason ``batch_disabled``."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=False,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_events: list[tuple[str, dict]] = []

    async def fake_audit(**kwargs):
        audit_events.append((kwargs["action"], kwargs.get("metadata") or {}))

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", _stub_child)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[_make_task(0), _make_task(1)],
        max_concurrent=8,
    )

    assert result.serial_fallback is True
    assert result.serial_fallback_reason == "batch_disabled"
    assert result.max_concurrent_used == 1
    assert result.completed == 2

    actions = [a for a, _ in audit_events]
    assert subagents_svc.AUDIT_BATCH_SERIAL_FALLBACK in actions
    started_meta = next(m for a, m in audit_events if a == subagents_svc.AUDIT_BATCH_STARTED)
    assert started_meta["serial_fallback_reason"] == "batch_disabled"


async def test_single_task_batch_logs_serial_single_task(monkeypatch):
    """A single-task batch always serializes regardless of policy."""
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

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", _stub_child)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[_make_task(0)],
    )

    assert result.total == 1
    assert result.completed == 1
    assert result.serial_fallback is True
    assert result.serial_fallback_reason == "single_task"


async def test_caller_max_concurrent_clamped_by_workspace_policy(monkeypatch):
    """Caller asks for 12 concurrent but workspace cap is 3 — service clamps to 3."""
    config = ResolvedSubagentBatchConfig(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=3,
        max_nesting_depth=3,
    )

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return config

    audit_events: list[tuple[str, dict]] = []

    async def fake_audit(**kwargs):
        audit_events.append((kwargs["action"], kwargs.get("metadata") or {}))

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)
    monkeypatch.setattr(subagents_svc, "_run_single_child", _stub_child)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[_make_task(i) for i in range(5)],
        max_concurrent=12,
    )

    assert result.total == 5
    assert result.completed == 5
    assert result.serial_fallback is False
    assert result.max_concurrent_used == 3


async def test_empty_task_list_is_noop(monkeypatch):
    """Empty batch returns immediately without auditing."""

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return ResolvedSubagentBatchConfig(
            batch_enabled=True,
            max_batch_size=20,
            max_concurrent=5,
            max_nesting_depth=3,
        )

    audit_calls: list[str] = []

    async def fake_audit(**kwargs):
        audit_calls.append(kwargs["action"])

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)
    monkeypatch.setattr(subagents_svc, "_audit", fake_audit)

    result = await subagents_svc.delegate_batch(
        parent_run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[],
    )
    assert result.total == 0
    assert result.results == {}
    assert audit_calls == []


async def test_duplicate_task_ids_raise_value_error(monkeypatch):
    """Duplicate task_ids inside one batch are rejected at the entry point."""

    async def fake_load(*, workspace_id: uuid.UUID) -> ResolvedSubagentBatchConfig:
        return ResolvedSubagentBatchConfig(
            batch_enabled=True,
            max_batch_size=20,
            max_concurrent=5,
            max_nesting_depth=3,
        )

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", fake_load)

    duplicate = subagents_svc.SubAgentTask(
        task_id="same",
        prompt="x",
        target_agent_id=uuid.uuid4(),
    )
    second = subagents_svc.SubAgentTask(
        task_id="same",
        prompt="y",
        target_agent_id=uuid.uuid4(),
    )

    with pytest.raises(ValueError, match="duplicate task_id"):
        await subagents_svc.delegate_batch(
            parent_run_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            parent_session_id=None,
            parent_identity_id=None,
            tasks=[duplicate, second],
        )


async def test_delegate_task_wraps_single_task(monkeypatch):
    """``delegate_task`` is a thin wrapper around ``delegate_batch``."""
    captured: dict[str, Any] = {}

    async def fake_batch(**kwargs):
        captured.update(kwargs)
        # Build a minimal BatchSpawnResult containing the result we
        # need to unwrap. The result map keys on ``task_id``.
        task_id = kwargs["tasks"][0].task_id
        result = subagents_svc.SubAgentResult(
            task_id=task_id,
            child_run_id=uuid.uuid4(),
            status="completed",
            output="ok",
            duration_ms=1,
        )
        return subagents_svc.BatchSpawnResult(
            parent_run_id=kwargs["parent_run_id"],
            total=1,
            completed=1,
            results={task_id: result},
            duration_ms=1,
            max_concurrent_used=1,
        )

    monkeypatch.setattr(subagents_svc, "delegate_batch", fake_batch)

    parent_run = uuid.uuid4()
    target = uuid.uuid4()
    result = await subagents_svc.delegate_task(
        parent_run_id=parent_run,
        workspace_id=uuid.uuid4(),
        parent_session_id=None,
        parent_identity_id=None,
        prompt="single",
        target_agent_id=target,
        task_id="solo",
    )
    assert result.task_id == "solo"
    assert result.status == "completed"
    assert captured["tasks"][0].task_id == "solo"
    assert captured["tasks"][0].target_agent_id == target
    assert captured["max_concurrent"] == 1


# Touch ``time`` so editors don't flag the import.
_ = time
