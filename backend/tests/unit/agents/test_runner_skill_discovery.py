"""``NativeBackend._injected_skill_ids`` lifecycle tests (M1.7).

The runner stashes the resolved skill pack id list keyed by ``run_id``
the moment ``_build_capabilities`` returns, so the M1.5 capture path
can read it without re-querying the DB. The dict is cleared in the
``run()`` ``finally`` so a long-running process can't leak entries.

These tests do not boot the agent loop — they exercise the public
helpers + a thin contract simulation around ``_build_capabilities`` so
the cleanup wiring stays under unit-test pressure.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest

from app.agents.kernels.base import RunEvent, RunEventKind, RunRequest
from app.agents.kernels.native.runner import NativeBackend

pytestmark = pytest.mark.asyncio


def _make_request() -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        user_text="hello",
        message_history=[],
        toolbox=[],
        skills=[],
        policy={},
    )


async def test_get_injected_skill_ids_default_empty():
    run_id = uuid.uuid4()
    assert NativeBackend.get_injected_skill_ids(run_id) == []


async def test_clear_is_idempotent_for_unknown_run():
    NativeBackend._clear_injected_skill_ids(uuid.uuid4())
    NativeBackend._clear_injected_skill_ids(uuid.uuid4())


async def test_stash_then_get_returns_ids_then_clear_drains():
    run_id = uuid.uuid4()
    pack_ids = [uuid.uuid4(), uuid.uuid4()]
    NativeBackend._injected_skill_ids[run_id] = pack_ids

    fetched = NativeBackend.get_injected_skill_ids(run_id)
    assert fetched == pack_ids
    fetched.append(uuid.uuid4())
    assert NativeBackend.get_injected_skill_ids(run_id) == pack_ids

    NativeBackend._clear_injected_skill_ids(run_id)
    assert NativeBackend.get_injected_skill_ids(run_id) == []


async def test_run_finally_clears_injected_ids():
    """``NativeBackend.run`` ``finally`` block clears the run's entry.

    We bypass the real ``_run_inner`` (which would need a model + DB)
    and instead replace it with an async generator that emits a single
    FINAL frame; the ``finally`` block on ``run`` is what we're
    testing — independent of any pydantic-ai plumbing.
    """
    req = _make_request()
    pack_ids = [uuid.uuid4()]
    NativeBackend._injected_skill_ids[req.run_id] = list(pack_ids)
    backend = NativeBackend()

    async def _fake_run_inner(self, request: RunRequest) -> AsyncIterator[RunEvent]:
        assert NativeBackend.get_injected_skill_ids(request.run_id) == pack_ids
        yield RunEvent(RunEventKind.FINAL, {"reason": "ok"})

    with patch.object(NativeBackend, "_run_inner", _fake_run_inner):
        events = [ev async for ev in backend.run(req)]

    assert len(events) == 1
    assert events[0].kind == RunEventKind.FINAL
    assert NativeBackend.get_injected_skill_ids(req.run_id) == []
    assert req.run_id not in NativeBackend._injected_skill_ids


async def test_run_finally_clears_even_on_exception():
    req = _make_request()
    NativeBackend._injected_skill_ids[req.run_id] = [uuid.uuid4()]
    backend = NativeBackend()

    async def _boom(self, request: RunRequest) -> AsyncIterator[RunEvent]:
        if False:  # type: ignore[unreachable]
            yield RunEvent(RunEventKind.FINAL, {})
        raise RuntimeError("simulated kernel crash")

    with (
        patch.object(NativeBackend, "_run_inner", _boom),
        pytest.raises(RuntimeError, match="simulated kernel crash"),
    ):
        async for _ in backend.run(req):
            pass

    assert NativeBackend.get_injected_skill_ids(req.run_id) == []


async def test_get_returns_independent_copy():
    run_id = uuid.uuid4()
    pack_ids = [uuid.uuid4(), uuid.uuid4()]
    NativeBackend._injected_skill_ids[run_id] = list(pack_ids)
    try:
        fetched = NativeBackend.get_injected_skill_ids(run_id)
        fetched.clear()
        assert NativeBackend._injected_skill_ids[run_id] == pack_ids
    finally:
        NativeBackend._clear_injected_skill_ids(run_id)
