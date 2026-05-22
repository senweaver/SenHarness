"""Pure-function tests for the M0.3 judge prompt rendering + parsing.

Avoids real LLM calls — exercises the pure helpers around
``call_aux_judge`` (turns serialiser, prompt renderer, defaults
merger) so the contract with the aux model is locked down.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.auxiliary_client import (
    DEFAULT_AUX_SETTINGS,
    JudgeVerdict,
    _serialise_artifact_turns,
    render_judge_user_prompt,
)


def test_serialise_artifact_turns_compacts_and_marks_tool_results():
    turns = [
        {
            "role": "user",
            "iteration": 0,
            "text": "find the population of Tokyo",
        },
        {
            "role": "assistant",
            "iteration": 1,
            "text": "Looking it up.",
            "tool_calls": [{"name": "search", "args": {"q": "Tokyo population"}}],
            "tool_results": [
                {"call_id": "1", "ok": True, "data": "13M"},
                {"call_id": "2", "ok": False, "error": "rate limited"},
            ],
        },
    ]
    rendered = _serialise_artifact_turns(turns, max_chars=500)
    assert "find the population" in rendered
    assert "tools: search" in rendered
    assert "ok=1 err=1" in rendered


def test_serialise_artifact_turns_truncates_to_budget():
    turns = [
        {
            "role": "assistant",
            "iteration": 1,
            "text": "x" * 2000,
        }
    ]
    rendered = _serialise_artifact_turns(turns, max_chars=200)
    assert len(rendered) <= 200
    assert "[truncated]" in rendered


def test_render_judge_user_prompt_includes_outcome_header():
    artifact = SimpleNamespace(
        final_outcome="success",
        error_kind=None,
        iteration_count=3,
        invoked_tools=["search", "fs.read"],
    )
    prompt = render_judge_user_prompt(
        artifact=artifact,
        turns_serialized="trace body here",
        max_chars=200,
    )
    assert "final_outcome=success" in prompt
    assert "tools=search,fs.read" in prompt
    assert "TRACE:\ntrace body here" in prompt


def test_judge_verdict_rejects_invalid_score():
    with pytest.raises(ValidationError):
        JudgeVerdict(score=2, confidence=0.5, rationale="x")


def test_judge_verdict_caps_rationale_length():
    with pytest.raises(ValidationError):
        JudgeVerdict(score=0, confidence=0.5, rationale="y" * 1000)


def test_default_aux_settings_have_required_keys():
    for key in (
        "aux_model_judge",
        "judge_rate_per_minute",
        "judge_fail_strikes",
        "judge_fail_window_seconds",
        "judge_breaker_recover_seconds",
    ):
        assert key in DEFAULT_AUX_SETTINGS, key
