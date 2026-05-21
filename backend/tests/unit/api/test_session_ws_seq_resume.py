"""WebSocket ``seq``-stamping + reconnect-replay coverage.

The session WS hands out monotonic ``seq`` numbers and keeps a ring buffer
so a reconnecting client can ask for "everything I missed" via the
``resume`` frame. These pure-Python tests exercise the helper duo
``_emit`` + ``_replay_cached_events`` without spinning up FastAPI's
TestClient — keeping them in the unit lane (no DB / no socket).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.api.v1.sessions import (
    _emit,
    _new_ws_state,
    _replay_cached_events,
)


class _StubWs:
    """Captures every ``send_json`` call so assertions can inspect the wire format."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_emit_assigns_monotonic_seq() -> None:
    ws = _StubWs()
    state = _new_ws_state()

    await _emit(ws, state, {"type": "delta", "data": {"text": "a"}})
    await _emit(ws, state, {"type": "delta", "data": {"text": "b"}})
    await _emit(ws, state, {"type": "final", "data": {"message_id": "m1"}})

    seqs = [frame["data"]["seq"] for frame in ws.sent]
    assert seqs == [1, 2, 3], "seq must increment per frame, starting at 1"


@pytest.mark.asyncio
async def test_emit_stamps_active_run_id() -> None:
    """When a turn is in flight, every emitted frame carries the run_id so a
    reconnect can scope the replay to that specific run."""
    ws = _StubWs()
    state = _new_ws_state()
    state["current_run_id"] = uuid.uuid4()

    await _emit(ws, state, {"type": "delta", "data": {"text": "hi"}})
    frame = ws.sent[-1]
    assert frame["data"]["run_id"] == str(state["current_run_id"])


@pytest.mark.asyncio
async def test_emit_does_not_clobber_explicit_run_id() -> None:
    """Caller-supplied run_id wins (e.g. a non-active-run admin frame)."""
    ws = _StubWs()
    state = _new_ws_state()
    state["current_run_id"] = uuid.uuid4()
    explicit = str(uuid.uuid4())

    await _emit(
        ws,
        state,
        {"type": "approval_update", "data": {"id": "x", "run_id": explicit}},
    )
    assert ws.sent[-1]["data"]["run_id"] == explicit


@pytest.mark.asyncio
async def test_replay_cached_events_replays_after_last_seen() -> None:
    """A reconnect with last_seen_seq=2 should re-emit only seq>2."""
    ws = _StubWs()
    state = _new_ws_state()

    for t in ("a", "b", "c", "d"):
        await _emit(ws, state, {"type": "delta", "data": {"text": t}})
    # Wipe the live send log; replay should land here.
    ws.sent.clear()

    replayed = await _replay_cached_events(
        ws, ws_state=state, last_seen_seq=2, run_id=None
    )
    assert replayed == 2
    seqs = [frame["data"]["seq"] for frame in ws.sent]
    assert seqs == [3, 4]
    texts = [frame["data"]["text"] for frame in ws.sent]
    assert texts == ["c", "d"]


@pytest.mark.asyncio
async def test_replay_filters_by_run_id() -> None:
    ws = _StubWs()
    state = _new_ws_state()

    run_a = uuid.uuid4()
    run_b = uuid.uuid4()

    state["current_run_id"] = run_a
    await _emit(ws, state, {"type": "delta", "data": {"text": "a1"}})
    await _emit(ws, state, {"type": "delta", "data": {"text": "a2"}})

    state["current_run_id"] = run_b
    await _emit(ws, state, {"type": "delta", "data": {"text": "b1"}})

    ws.sent.clear()
    replayed = await _replay_cached_events(
        ws, ws_state=state, last_seen_seq=0, run_id=run_b
    )
    assert replayed == 1
    assert ws.sent[0]["data"]["text"] == "b1"


@pytest.mark.asyncio
async def test_replay_missing_seq_treated_as_zero() -> None:
    ws = _StubWs()
    state = _new_ws_state()

    await _emit(ws, state, {"type": "delta", "data": {"text": "x"}})
    ws.sent.clear()

    replayed = await _replay_cached_events(
        ws, ws_state=state, last_seen_seq=None, run_id=None
    )
    assert replayed == 1


@pytest.mark.asyncio
async def test_emit_send_lock_serialises_concurrent_writers() -> None:
    """Two coroutines emitting in parallel must not interleave the seq
    counter — the lock guarantees frame N+1's wire payload reflects the
    next-after-N value, not a torn read."""
    ws = _StubWs()
    state = _new_ws_state()

    async def burst(prefix: str, n: int) -> None:
        for i in range(n):
            await _emit(ws, state, {"type": "delta", "data": {"text": f"{prefix}{i}"}})

    await asyncio.gather(burst("a", 5), burst("b", 5))
    seqs = [frame["data"]["seq"] for frame in ws.sent]
    assert sorted(seqs) == list(range(1, 11)), "every seq used exactly once"
    assert seqs == sorted(seqs), "seqs emitted in non-decreasing order"
