"""Reasoning archetype resolution + runtime gating (model_profile)."""

from __future__ import annotations

from app.agents.kernels.model_profile import (
    apply_reasoning_settings,
    resolve_profile,
)


def _archetype(supported: bool, hybrid: bool) -> str:
    if not supported:
        return "none"
    return "hybrid" if hybrid else "always"


def test_pure_reasoner_is_always_without_effort() -> None:
    profile = resolve_profile(provider_kind="deepseek", model_name="deepseek-reasoner")
    assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "always"
    assert profile.reasoning.supports_effort is False


def test_kimi_thinking_rejects_effort() -> None:
    profile = resolve_profile(provider_kind="moonshot", model_name="kimi-k2-thinking")
    assert profile.reasoning.supported is True
    assert profile.reasoning.hybrid is False
    assert profile.reasoning.supports_effort is False


def test_qwen3_is_hybrid_without_effort() -> None:
    profile = resolve_profile(provider_kind="dashscope", model_name="qwen3-235b")
    assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "hybrid"
    assert profile.reasoning.supports_effort is False


def test_deepseek_v4_is_hybrid_without_effort() -> None:
    profile = resolve_profile(provider_kind="deepseek", model_name="deepseek-v4-chat")
    assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "hybrid"
    assert profile.reasoning.supports_effort is False


def test_glm5_is_hybrid_with_effort() -> None:
    profile = resolve_profile(provider_kind="zhipu", model_name="glm-5-air")
    assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "hybrid"
    assert profile.reasoning.supports_effort is True


def test_openai_o_series_supports_effort() -> None:
    for name in ("o3", "o4-mini"):
        profile = resolve_profile(provider_kind="openai", model_name=name)
        assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "always"
        assert profile.reasoning.supports_effort is True


def test_unknown_model_is_none_archetype() -> None:
    profile = resolve_profile(provider_kind="openai", model_name="gpt-4o-mini")
    assert profile.reasoning.supported is False
    assert _archetype(profile.reasoning.supported, profile.reasoning.hybrid) == "none"


def test_db_override_toggles_supports_effort() -> None:
    profile = resolve_profile(
        provider_kind="dashscope",
        model_name="qwen3-235b",
        db_metadata={"profile": {"reasoning": {"supports_effort": True}}},
    )
    assert profile.reasoning.hybrid is True
    assert profile.reasoning.supports_effort is True


def test_unsupported_model_emits_no_payload_and_strips_effort() -> None:
    profile = resolve_profile(provider_kind="openai", model_name="gpt-4o-mini")
    settings: dict = {"reasoning_effort": "high"}
    apply_reasoning_settings(
        profile=profile,
        model_settings=settings,
        reasoning_payload={},
        thinking_state="on",
        run_effort="high",
    )
    assert "reasoning_effort" not in settings
    assert settings == {}


def test_effort_gated_off_for_reasoner_without_effort() -> None:
    profile = resolve_profile(provider_kind="deepseek", model_name="deepseek-reasoner")
    settings: dict = {}
    apply_reasoning_settings(
        profile=profile,
        model_settings=settings,
        reasoning_payload=profile.reasoning.enable,
        thinking_state="on",
        run_effort="high",
    )
    assert "reasoning_effort" not in settings


def test_effort_applied_for_effort_capable_reasoner() -> None:
    profile = resolve_profile(provider_kind="openai", model_name="o3")
    settings: dict = {}
    apply_reasoning_settings(
        profile=profile,
        model_settings=settings,
        reasoning_payload=profile.reasoning.enable,
        thinking_state="on",
        run_effort="high",
    )
    assert settings["reasoning_effort"] == "high"


def test_hybrid_off_strips_effort_for_tool_call_unsafe() -> None:
    profile = resolve_profile(provider_kind="zhipu", model_name="glm-5-air")
    settings: dict = {}
    apply_reasoning_settings(
        profile=profile,
        model_settings=settings,
        reasoning_payload=profile.reasoning.disable,
        thinking_state="off",
        run_effort="medium",
    )
    assert "reasoning_effort" not in settings
