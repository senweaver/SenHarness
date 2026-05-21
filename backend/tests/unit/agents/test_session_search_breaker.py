"""Breaker + rate gate tests for the M2.5.8 session_search summarise path.

Three concerns covered:

1. ``is_breaker_open=True`` short-circuits before the aux call.
2. Three consecutive aux failures bump the strike count to the trip
   threshold and emit ``summarize.breaker_tripped`` audit.
3. The rate gate denying the call short-circuits with the same fallback
   shape as the breaker.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.agents.tools import session_search as tool
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.session_search import (
    AUDIT_BREAKER_TRIPPED,
    AUDIT_FALLBACK,
    SessionSearchArgs,
    run_session_search,
)


pytestmark = pytest.mark.asyncio


def _install_settings(monkeypatch: pytest.MonkeyPatch, *, strikes: int = 3) -> None:
    async def _stub_settings(db, *, workspace_id):  # noqa: ARG001
        return {
            "summarize_rate_per_minute": 30,
            "summarize_fail_strikes": strikes,
            "summarize_fail_window_seconds": 300,
            "summarize_breaker_recover_seconds": 1800,
        }

    monkeypatch.setattr(tool, "get_workspace_aux_settings", _stub_settings)


def _make_context() -> ToolRunContext:
    ctx = ToolRunContext(
        run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        scratch_base=Path("."),
        policy={},
    )
    set_context(ctx)
    return ctx


def _hits(n: int) -> list[dict]:
    return [
        {
            "message_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "session_title": "s",
            "role": "user",
            "created_at": "2026-05-10T00:00:00",
            "score": 0.5,
            "body": "body",
        }
        for _ in range(n)
    ]


async def test_breaker_open_skips_aux_and_returns_raw(monkeypatch):
    audit_rows: list[dict] = []

    async def _record(**kwargs):
        audit_rows.append(kwargs)

    raw = _hits(3)
    aux_calls: list = []

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux(**_):
        aux_calls.append("called")
        return None

    async def _open(*, bucket, workspace_id, trip_at):  # noqa: ARG001
        return True

    async def _consume(*, bucket, workspace_id, limit, period_seconds=60):  # noqa: ARG001
        raise AssertionError("rate gate must not run when breaker is open")

    monkeypatch.setattr(tool, "_record_audit", _record)
    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux)
    monkeypatch.setattr(tool, "is_breaker_open", _open)
    monkeypatch.setattr(tool, "consume_rate", _consume)
    _install_settings(monkeypatch)

    _make_context()
    out = await run_session_search(SessionSearchArgs(query="q"))
    assert out["summarized"] is False
    assert out["fallback_reason"] == "breaker_open"
    assert out["hits"] == raw
    assert aux_calls == []
    actions = [row["action"] for row in audit_rows]
    assert AUDIT_FALLBACK in actions
    breaker_audit = next(r for r in audit_rows if r["action"] == AUDIT_FALLBACK)
    assert breaker_audit["metadata"]["reason"] == "breaker_open"


async def test_three_strikes_trip_breaker(monkeypatch):
    audit_rows: list[dict] = []
    breaker_state = {"strikes": 0}
    open_state = {"open": False}

    async def _record(**kwargs):
        audit_rows.append(kwargs)

    async def _is_open(*, bucket, workspace_id, trip_at):  # noqa: ARG001
        return open_state["open"]

    async def _consume(*, bucket, workspace_id, limit, period_seconds=60):  # noqa: ARG001
        return True

    async def _bump(*, bucket, workspace_id, window_seconds, recover_seconds=None):  # noqa: ARG001
        breaker_state["strikes"] += 1
        if breaker_state["strikes"] >= 3:
            open_state["open"] = True
        return breaker_state["strikes"]

    async def _reset(*, bucket, workspace_id):  # noqa: ARG001
        breaker_state["strikes"] = 0
        open_state["open"] = False

    raw = _hits(2)

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux_always_fail(**_):
        return None

    monkeypatch.setattr(tool, "_record_audit", _record)
    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux_always_fail)
    monkeypatch.setattr(tool, "is_breaker_open", _is_open)
    monkeypatch.setattr(tool, "consume_rate", _consume)
    monkeypatch.setattr(tool, "bump_failure", _bump)
    monkeypatch.setattr(tool, "reset_failure", _reset)
    _install_settings(monkeypatch, strikes=3)

    _make_context()

    for _ in range(3):
        out = await run_session_search(SessionSearchArgs(query="q"))
        assert out["summarized"] is False
        assert out["fallback_reason"] == "aux_failure"

    actions = [row["action"] for row in audit_rows]
    # AUDIT_BREAKER_TRIPPED only fires when the strike count crosses
    # the trip_at threshold — exactly once on the third attempt.
    assert actions.count(AUDIT_BREAKER_TRIPPED) == 1
    tripped = next(r for r in audit_rows if r["action"] == AUDIT_BREAKER_TRIPPED)
    assert tripped["metadata"]["strikes"] == 3
    assert tripped["metadata"]["trip_at"] == 3

    # Fourth call sees breaker open and short-circuits before bump.
    out = await run_session_search(SessionSearchArgs(query="q"))
    assert out["fallback_reason"] == "breaker_open"


async def test_rate_gate_denies_with_audit(monkeypatch):
    audit_rows: list[dict] = []

    async def _record(**kwargs):
        audit_rows.append(kwargs)

    raw = _hits(2)
    aux_calls: list = []

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux(**_):
        aux_calls.append("called")
        return None

    async def _open(*, bucket, workspace_id, trip_at):  # noqa: ARG001
        return False

    async def _consume_deny(*, bucket, workspace_id, limit, period_seconds=60):  # noqa: ARG001
        return False

    monkeypatch.setattr(tool, "_record_audit", _record)
    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux)
    monkeypatch.setattr(tool, "is_breaker_open", _open)
    monkeypatch.setattr(tool, "consume_rate", _consume_deny)
    _install_settings(monkeypatch)

    _make_context()
    out = await run_session_search(SessionSearchArgs(query="q"))
    assert out["summarized"] is False
    assert out["fallback_reason"] == "rate_limited"
    assert aux_calls == []
    actions = [row["action"] for row in audit_rows]
    assert AUDIT_FALLBACK in actions
    rate_audit = next(r for r in audit_rows if r["action"] == AUDIT_FALLBACK)
    assert rate_audit["metadata"]["reason"] == "rate_limited"
    assert rate_audit["metadata"]["limit_per_minute"] == 30


async def test_breaker_bucket_is_independent_from_judge():
    """Sanity test on the constant — keeps the bucket name from sliding
    into ``judge`` / ``evolver`` and pulling another module's strikes.
    """
    assert tool.SUMMARIZE_BREAKER_BUCKET == "summarize"
    assert tool.SUMMARIZE_RATE_BUCKET == "summarize"
