"""Integration: M2.5.6 batch spawn fan-out + reliability invariants.

End-to-end coverage that needs a real Postgres so the spine table
(``subagent_runs``) is the source of truth. Three cases:

* 5 tasks + max_concurrent=3 → 5 SubAgentRun rows share the same
  ``parent_run_id`` and the per-run repository can pull them all back
  via ``list_by_parent_run_id``.
* 1 task fails (model raises) + 4 succeed → BatchSpawnResult tallies
  ``completed=4`` + ``failed=1`` and the failed child's spine row
  lands in FAILED with the right ``error_kind``.
* 1 task times out → BatchSpawnResult shows ``timed_out=1`` and that
  child's spine row carries ``error_kind='timeout'``.

Tests stub ``_run_single_child`` selectively when they need
deterministic per-child outcomes; one case uses a real
``_run_single_child`` against a stubbed model resolver to exercise
the full lifecycle (register_run + transition_state + gate skip).

Pattern note: every short-lived ``get_session_factory()`` opened by
the harness lifecycle hooks is rerouted to the test's ``db_session``
fixture so all rows land inside the same transaction (no FK conflict
against the rolled-back ``workspace`` fixture).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest

from app.agents.harness import subagents as subagents_svc
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.repositories.subagent_run import SubAgentRunRepository
from app.services.subagent_batch_config import ResolvedSubagentBatchConfig

pytestmark = pytest.mark.asyncio


def _config(**overrides) -> ResolvedSubagentBatchConfig:
    base = dict(
        batch_enabled=True,
        max_batch_size=20,
        max_concurrent=5,
        max_nesting_depth=3,
    )
    base.update(overrides)
    return ResolvedSubagentBatchConfig(**base)


def _share_session_factory(monkeypatch, db_session) -> None:
    """Reroute every short-lived session opened by ``subagents.py`` and
    its lifecycle helpers through the test's ``db_session`` so the
    spine rows land where the test assertions can read them.

    A lock serializes DB access: ``delegate_batch`` runs children
    concurrently but lifecycle hooks each ``commit()`` on the shared
    session — SQLAlchemy ``AsyncSession`` is not safe for overlapping
    use from multiple tasks.
    """
    lock = asyncio.Lock()

    @asynccontextmanager
    async def _ctx():
        async with lock:
            yield db_session

    def _factory():
        return _ctx

    monkeypatch.setattr("app.db.session.get_session_factory", _factory)


async def _patch_config(monkeypatch, config: ResolvedSubagentBatchConfig) -> None:
    async def _load(*, workspace_id):
        return config

    monkeypatch.setattr(subagents_svc, "_load_resolved_config", _load)


async def test_batch_writes_one_spine_row_per_task_sharing_parent_run(
    db_session, workspace, monkeypatch
):
    """5 children, max_concurrent=3 → 5 rows + parent_run_id query
    returns all 5 in a single fetch."""
    _share_session_factory(monkeypatch, db_session)
    await _patch_config(monkeypatch, _config(max_concurrent=3))

    parent_run_id = uuid.uuid4()
    workspace_id = workspace.id

    fake_agent_id = uuid.uuid4()

    class _FakeRunResult:
        output = "the answer is 42"

    class _FakeAgent:
        async def run(self, prompt: str):
            await asyncio.sleep(0.01)
            return _FakeRunResult()

    async def _fake_resolve(*, workspace_id, target_agent_id):
        return object(), None

    monkeypatch.setattr(subagents_svc, "_resolve_child_agent_model", _fake_resolve)
    monkeypatch.setattr(
        subagents_svc,
        "_build_child_agent",
        lambda *, model, persona_md: _FakeAgent(),
    )

    tasks = [
        subagents_svc.SubAgentTask(
            task_id=f"task-{i}",
            prompt=f"prompt {i}",
            target_agent_id=fake_agent_id,
        )
        for i in range(5)
    ]

    summary = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=tasks,
        max_concurrent=3,
        skip_hallucination_gate=True,
    )
    assert summary.total == 5
    assert summary.completed == 5
    assert summary.serial_fallback is False
    assert summary.max_concurrent_used == 3
    assert set(summary.results.keys()) == {f"task-{i}" for i in range(5)}

    repo = SubAgentRunRepository(db_session)
    rows = list(await repo.list_by_parent_run_id(parent_run_id=parent_run_id))
    assert len(rows) == 5
    states = {r.state for r in rows}
    assert states == {SubAgentRunState.COMPLETED}


async def test_one_failed_child_does_not_block_others(db_session, workspace, monkeypatch):
    """1 child raises mid-run → 4 still complete + 1 lands FAILED."""
    _share_session_factory(monkeypatch, db_session)
    await _patch_config(monkeypatch, _config(max_concurrent=5))

    parent_run_id = uuid.uuid4()
    workspace_id = workspace.id

    class _Crash(RuntimeError):
        pass

    class _AgentFor:
        def __init__(self, *, will_crash: bool) -> None:
            self.will_crash = will_crash

        async def run(self, prompt: str):
            await asyncio.sleep(0.005)
            if self.will_crash:
                raise _Crash("simulated child crash")

            class _Out:
                output = "ok"

            return _Out()

    crash_for = "task-2"

    async def _fake_resolve(*, workspace_id, target_agent_id):
        # Encode the target_agent_id as a key the builder closure can
        # pick up below — passes through without per-task monkeypatch.
        return target_agent_id, None

    monkeypatch.setattr(subagents_svc, "_resolve_child_agent_model", _fake_resolve)

    crash_target_ids: set[uuid.UUID] = set()

    def _build(*, model, persona_md):
        return _AgentFor(will_crash=model in crash_target_ids)

    monkeypatch.setattr(subagents_svc, "_build_child_agent", _build)

    tasks: list[subagents_svc.SubAgentTask] = []
    for i in range(5):
        target = uuid.uuid4()
        if f"task-{i}" == crash_for:
            crash_target_ids.add(target)
        tasks.append(
            subagents_svc.SubAgentTask(
                task_id=f"task-{i}",
                prompt=f"p {i}",
                target_agent_id=target,
            )
        )

    summary = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=tasks,
        max_concurrent=5,
        skip_hallucination_gate=True,
    )
    assert summary.total == 5
    assert summary.completed == 4
    assert summary.failed == 1

    crashed_result = summary.results[crash_for]
    assert crashed_result.status == "failed"
    assert crashed_result.error_kind == "_Crash"

    repo = SubAgentRunRepository(db_session)
    row = await repo.get_by_child_run_id(child_run_id=crashed_result.child_run_id)
    assert row is not None
    assert row.state == SubAgentRunState.FAILED


async def test_one_timeout_child_marks_status_timeout(db_session, workspace, monkeypatch):
    """A single timeout sibling shows ``status='timeout'`` + spine FAILED."""
    _share_session_factory(monkeypatch, db_session)
    await _patch_config(monkeypatch, _config(max_concurrent=3))

    parent_run_id = uuid.uuid4()
    workspace_id = workspace.id

    class _SlowAgent:
        async def run(self, prompt: str):
            await asyncio.sleep(2.0)

            class _Out:
                output = "late"

            return _Out()

    class _FastAgent:
        async def run(self, prompt: str):
            await asyncio.sleep(0.01)

            class _Out:
                output = "fast"

            return _Out()

    slow_target_ids: set[uuid.UUID] = set()

    async def _fake_resolve(*, workspace_id, target_agent_id):
        # Tunnel target_agent_id into the builder closure as the
        # "model" so the builder can choose Slow vs Fast deterministically.
        return target_agent_id, None

    monkeypatch.setattr(subagents_svc, "_resolve_child_agent_model", _fake_resolve)

    def _build(*, model, persona_md):
        if model in slow_target_ids:
            return _SlowAgent()
        return _FastAgent()

    monkeypatch.setattr(subagents_svc, "_build_child_agent", _build)

    slow_target = uuid.uuid4()
    slow_target_ids.add(slow_target)

    fast_a = subagents_svc.SubAgentTask(
        task_id="task-fast-1",
        prompt="p",
        target_agent_id=uuid.uuid4(),
        timeout_seconds=10,
    )
    fast_b = subagents_svc.SubAgentTask(
        task_id="task-fast-2",
        prompt="p",
        target_agent_id=uuid.uuid4(),
        timeout_seconds=10,
    )
    slow_task = "task-slow"
    slow = subagents_svc.SubAgentTask(
        task_id=slow_task,
        prompt="p",
        target_agent_id=slow_target,
        timeout_seconds=1,
    )

    summary = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=[fast_a, slow, fast_b],
        max_concurrent=3,
        skip_hallucination_gate=True,
    )

    assert summary.total == 3
    assert summary.timed_out == 1
    assert summary.completed == 2
    slow_result = summary.results[slow_task]
    assert slow_result.status == "timeout"
    assert slow_result.error_kind == "timeout"

    repo = SubAgentRunRepository(db_session)
    row = await repo.get_by_child_run_id(child_run_id=slow_result.child_run_id)
    assert row is not None
    # Timeout drives FAILED via on_child_failed (not ZOMBIE — that's
    # the reaper's path for hung-but-still-running children).
    assert row.state == SubAgentRunState.FAILED
    assert row.error_kind == "timeout"


async def test_max_concurrent_caps_in_flight(monkeypatch, db_session, workspace):
    """Concurrency cap actually limits in-flight children at runtime."""
    _share_session_factory(monkeypatch, db_session)
    await _patch_config(monkeypatch, _config(max_concurrent=2))

    parent_run_id = uuid.uuid4()
    workspace_id = workspace.id

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _fake_child(*, task, **kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.05)
            return subagents_svc.SubAgentResult(
                task_id=task.task_id,
                child_run_id=uuid.uuid4(),
                status="completed",
            )
        finally:
            async with lock:
                in_flight -= 1

    monkeypatch.setattr(subagents_svc, "_run_single_child", _fake_child)

    tasks = [
        subagents_svc.SubAgentTask(
            task_id=f"task-{i}",
            prompt="p",
            target_agent_id=uuid.uuid4(),
        )
        for i in range(6)
    ]

    summary = await subagents_svc.delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=None,
        parent_identity_id=None,
        tasks=tasks,
        max_concurrent=2,
    )

    assert summary.total == 6
    assert summary.completed == 6
    assert peak <= 2


# Touch SubAgentRun so editors don't flag the import (used implicitly via repo).
_ = SubAgentRun
