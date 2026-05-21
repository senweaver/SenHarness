"""resolve_reasoning_effort_for_run — flash must not imply adaptive effort."""

from __future__ import annotations

from app.agents.kernels.native.runner import resolve_reasoning_effort_for_run


def test_flash_mode_returns_none_even_with_analytical_prompt() -> None:
    policy = {"mode": "flash"}
    r = resolve_reasoning_effort_for_run(
        policy=policy,
        user_text="Plan and analyze the architecture in detail.",
    )
    assert r is None


def test_explicit_thinking_high_wins_over_flash_mode_key() -> None:
    policy = {"mode": "flash", "reasoning_effort": "high"}
    r = resolve_reasoning_effort_for_run(policy=policy, user_text="hi")
    assert r == "high"


def test_adaptive_when_not_flash_and_no_explicit() -> None:
    policy: dict = {"reliability": {"adaptive_reasoning": True}}
    r = resolve_reasoning_effort_for_run(
        policy=policy,
        user_text="Please analyze and compare all tradeoffs.",
    )
    assert r == "high"
