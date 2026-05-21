"""Pure tests for the M2.5.8 session_search summarise path.

Stubs the ts_vector helper, the aux LLM call, and the audit + breaker
hooks so the tool body can be exercised without Postgres or Redis.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.agents.tools import session_search as tool
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.session_search import (
    AUDIT_EVIDENCE_FILTERED,
    AUDIT_FALLBACK,
    AUDIT_INVOKED,
    SessionSearchArgs,
    SessionSearchSummary,
    run_session_search,
)


pytestmark = pytest.mark.asyncio


# ─── Test harness helpers ────────────────────────────────────
class _StubAux:
    """Captures every audit row so tests can assert on action keys."""

    def __init__(self) -> None:
        self.audit_rows: list[dict] = []
        self.breaker_state: dict[str, int] = {}
        self.reset_calls: list[str] = []
        self.rate_allow: bool = True

    async def record(
        self,
        *,
        action,
        workspace_id,
        actor_identity_id,
        summary_text,
        metadata,
    ) -> None:
        self.audit_rows.append(
            {
                "action": action,
                "workspace_id": workspace_id,
                "actor_identity_id": actor_identity_id,
                "summary": summary_text,
                "metadata": dict(metadata),
            }
        )

    async def is_breaker_open(self, *, bucket, workspace_id, trip_at) -> bool:
        return self.breaker_state.get(workspace_id, 0) >= trip_at

    async def consume_rate(
        self, *, bucket, workspace_id, limit, period_seconds=60
    ) -> bool:
        return self.rate_allow

    async def bump_failure(
        self, *, bucket, workspace_id, window_seconds, recover_seconds=None
    ) -> int:
        self.breaker_state[workspace_id] = self.breaker_state.get(workspace_id, 0) + 1
        return self.breaker_state[workspace_id]

    async def reset_failure(self, *, bucket, workspace_id) -> None:
        self.reset_calls.append(workspace_id)
        self.breaker_state.pop(workspace_id, None)


def _install_stubs(monkeypatch: pytest.MonkeyPatch, aux: _StubAux) -> None:
    monkeypatch.setattr(tool, "_record_audit", aux.record)
    monkeypatch.setattr(tool, "is_breaker_open", aux.is_breaker_open)
    monkeypatch.setattr(tool, "consume_rate", aux.consume_rate)
    monkeypatch.setattr(tool, "bump_failure", aux.bump_failure)
    monkeypatch.setattr(tool, "reset_failure", aux.reset_failure)

    async def _stub_settings(db, *, workspace_id):  # noqa: ARG001
        return {
            "summarize_rate_per_minute": 30,
            "summarize_fail_strikes": 3,
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
            "session_title": f"session-{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "created_at": "2026-05-10T12:00:00",
            "score": 0.8 - (i * 0.05),
            "body": f"hit body {i} about deployment runbook",
        }
        for i in range(n)
    ]


# ─── Tests ───────────────────────────────────────────────────
async def test_summarize_false_returns_raw_hits_only(monkeypatch):
    aux = _StubAux()
    _install_stubs(monkeypatch, aux)

    raw = _hits(3)

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)

    _make_context()
    out = await run_session_search(
        SessionSearchArgs(query="deploy", summarize=False)
    )
    assert out["summarized"] is False
    assert out["hits"] == raw
    # No aux call → no audit rows.
    assert aux.audit_rows == []


async def test_summarize_true_with_zero_hits_skips_aux(monkeypatch):
    aux = _StubAux()
    _install_stubs(monkeypatch, aux)

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return []

    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    aux_calls: list = []

    async def _stub_aux(**_):
        aux_calls.append("called")
        return None

    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux)

    _make_context()
    out = await run_session_search(SessionSearchArgs(query="never", summarize=True))
    assert out["summarized"] is False
    assert out["hits"] == []
    assert aux_calls == []
    assert aux.audit_rows == []


async def test_summarize_happy_path_returns_summary_and_filters_evidence(
    monkeypatch,
):
    aux = _StubAux()
    _install_stubs(monkeypatch, aux)

    raw = _hits(5)
    real_id = uuid.UUID(raw[1]["message_id"])
    fake_id = uuid.uuid4()  # not in raw → must be filtered out

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux(**kwargs):  # noqa: ARG001
        return SessionSearchSummary(
            summary="The team agreed on Tuesday deployments.",
            bullet_points=["Use blue-green", "Notify #ops"],
            evidence_message_ids=[real_id, fake_id],
        )

    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux)

    ctx = _make_context()
    out = await run_session_search(
        SessionSearchArgs(query="deployment", summarize=True)
    )
    assert out["summarized"] is True
    assert out["summary"].startswith("The team agreed")
    assert out["bullet_points"] == ["Use blue-green", "Notify #ops"]
    assert out["evidence_message_ids"] == [str(real_id)]
    assert out["based_on_count"] == 5
    assert out["raw_results"] == raw

    actions = [row["action"] for row in aux.audit_rows]
    assert AUDIT_EVIDENCE_FILTERED in actions
    assert AUDIT_INVOKED in actions
    invoked = next(r for r in aux.audit_rows if r["action"] == AUDIT_INVOKED)
    assert invoked["workspace_id"] == ctx.workspace_id
    assert invoked["metadata"]["evidence_count"] == 1
    assert invoked["metadata"]["evidence_filtered"] == 1
    assert invoked["metadata"]["hit_count"] == 5
    assert invoked["metadata"]["bullet_count"] == 2
    # Reset breaker on success.
    assert aux.reset_calls == [str(ctx.workspace_id)]


async def test_summary_truncated_to_caller_max_chars(monkeypatch):
    aux = _StubAux()
    _install_stubs(monkeypatch, aux)
    raw = _hits(2)

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux(**kwargs):  # noqa: ARG001
        return SessionSearchSummary(
            summary="x" * 1500,
            bullet_points=[],
            evidence_message_ids=[],
        )

    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux)

    _make_context()
    out = await run_session_search(
        SessionSearchArgs(query="q", summarize=True, summary_max_chars=200)
    )
    assert out["summarized"] is True
    assert len(out["summary"]) <= 200


async def test_aux_failure_falls_back_to_raw_and_audits(monkeypatch):
    aux = _StubAux()
    _install_stubs(monkeypatch, aux)
    raw = _hits(4)

    async def _stub_search(args, *, workspace_id):  # noqa: ARG001
        return raw

    async def _stub_aux_fail(**_):
        return None

    monkeypatch.setattr(tool, "_run_raw_search", _stub_search)
    monkeypatch.setattr(tool, "_summarise_hits", _stub_aux_fail)

    ctx = _make_context()
    out = await run_session_search(
        SessionSearchArgs(query="q", summarize=True)
    )
    assert out["summarized"] is False
    assert out["fallback_reason"] == "aux_failure"
    assert out["hits"] == raw

    actions = [row["action"] for row in aux.audit_rows]
    assert AUDIT_FALLBACK in actions
    fallback_meta = next(
        r for r in aux.audit_rows if r["action"] == AUDIT_FALLBACK
    )["metadata"]
    assert fallback_meta["reason"] == "aux_failure"
    assert fallback_meta["hit_count"] == 4
    # One strike now on the breaker, no reset.
    assert aux.breaker_state[str(ctx.workspace_id)] == 1
    assert aux.reset_calls == []


async def test_default_args_have_summarize_true():
    args = SessionSearchArgs(query="anything")
    assert args.summarize is True
    assert args.summary_max_chars == 800
    assert args.limit == 10


async def test_summary_max_chars_bounds():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SessionSearchArgs(query="q", summary_max_chars=50)
    with pytest.raises(ValidationError):
        SessionSearchArgs(query="q", summary_max_chars=10_000)
