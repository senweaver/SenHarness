"""Unit tests for the reflection decision logic on ``ReliabilityState``.

Covers M0.4 (periodic) + M0.5 (tool-call) triggers, the at-most-once-per-iter
contract, the disabled short-circuit, and the prompt-truncation marker.
"""

from __future__ import annotations

from app.agents.harness.reliability import (
    ReflectionConfig,
    ReflectionKind,
    build_state,
    resolve_reflection_config,
)


def _state(cfg: ReflectionConfig | None) -> object:
    state = build_state(policy={}, max_iterations=12, reflection_config=cfg)
    return state


def test_periodic_fires_at_interval() -> None:
    cfg = ReflectionConfig(interval_iterations=8, interval_tool_calls=999)
    state = _state(cfg)
    for _ in range(7):
        state.tick_iteration()
        decision = state.should_reflect()
        assert decision.should_inject is False, decision.reason

    state.tick_iteration()
    decision = state.should_reflect()
    assert decision.should_inject is True
    assert decision.kind == ReflectionKind.PERIODIC
    assert decision.rendered_prompt is not None
    assert "8" in decision.rendered_prompt
    assert decision.reason == "iter=8"


def test_tool_call_fires_at_threshold() -> None:
    cfg = ReflectionConfig(interval_iterations=999, interval_tool_calls=15)
    state = _state(cfg)
    for _ in range(14):
        state.tick_iteration()
        state.tick_tool_call()
        assert state.should_reflect().should_inject is False

    state.tick_iteration()
    state.tick_tool_call()
    decision = state.should_reflect()
    assert decision.should_inject is True
    assert decision.kind == ReflectionKind.TOOL_CALL
    assert "15" in (decision.rendered_prompt or "")
    assert decision.reason == "tools=15"


def test_disabled_is_silent_noop() -> None:
    cfg = ReflectionConfig(enabled=False, interval_iterations=1)
    state = _state(cfg)
    for _ in range(20):
        state.tick_iteration()
        state.tick_tool_call()
        decision = state.should_reflect()
        assert decision.should_inject is False
        assert decision.reason == "skip:disabled"


def test_no_config_is_silent_noop() -> None:
    state = _state(None)
    for _ in range(20):
        state.tick_iteration()
        state.tick_tool_call()
        decision = state.should_reflect()
        assert decision.should_inject is False
        assert decision.reason == "skip:disabled"


def test_at_most_once_per_iteration() -> None:
    cfg = ReflectionConfig(interval_iterations=2, interval_tool_calls=2)
    state = _state(cfg)
    state.tick_iteration()
    state.tick_iteration()
    state.tick_tool_call()
    state.tick_tool_call()
    first = state.should_reflect()
    assert first.should_inject is True
    second = state.should_reflect()
    assert second.should_inject is False
    assert second.reason == "skip:already_this_iteration"


def test_periodic_wins_tie() -> None:
    cfg = ReflectionConfig(interval_iterations=3, interval_tool_calls=3)
    state = _state(cfg)
    for _ in range(3):
        state.tick_iteration()
        state.tick_tool_call()
    decision = state.should_reflect()
    assert decision.should_inject is True
    assert decision.kind == ReflectionKind.PERIODIC


def test_truncation_marker_in_reason() -> None:
    cfg = ReflectionConfig(interval_iterations=1, interval_tool_calls=999, max_prompt_chars=20)
    state = _state(cfg)
    state.tick_iteration()
    decision = state.should_reflect()
    assert decision.should_inject is True
    assert decision.truncated is True
    assert decision.rendered_prompt is not None
    assert len(decision.rendered_prompt) <= 20
    assert "truncated" in (decision.reason or "")


def test_template_missing_degrades_gracefully(monkeypatch) -> None:
    cfg = ReflectionConfig(
        interval_iterations=1,
        interval_tool_calls=999,
        periodic_template="bogus",
    )
    state = _state(cfg)
    state.tick_iteration()
    decision = state.should_reflect()
    assert decision.should_inject is False
    assert decision.reason == "skip:template_missing"


def test_recent_tool_summary_caps_count() -> None:
    cfg = ReflectionConfig(interval_iterations=999, interval_tool_calls=999)
    state = _state(cfg)
    for i in range(20):
        state.record_tool_outcome(f"tool_{i}", {"i": i}, ok=(i % 2 == 0))
    summary = state.recent_tool_summary(max_count=5)
    assert len(summary) == 5
    assert summary[-1]["name"] == "tool_19"
    assert summary[-1]["ok"] is False


def test_disabled_skips_template_load(monkeypatch) -> None:
    """Disabled config must not touch the template loader (zero-IO path)."""
    cfg = ReflectionConfig(enabled=False, interval_iterations=1)
    state = _state(cfg)

    def boom(*args, **kwargs):  # pragma: no cover - executed only on failure
        raise AssertionError("loader was called on disabled path")

    from app.agents.templates.reflection import loader as loader_mod

    monkeypatch.setattr(loader_mod, "load_reflection_template", boom)
    state.tick_iteration()
    state.should_reflect()


# ─── Config merge precedence ──────────────────────────────────────
def test_resolve_uses_workspace_then_agent_override() -> None:
    workspace = {"reflection": {"interval_iterations": 4, "enabled": True}}
    agent = {"reflection": {"interval_iterations": 2}}
    cfg = resolve_reflection_config(workspace_settings=workspace, agent_policy=agent)
    assert cfg.interval_iterations == 2
    assert cfg.enabled is True


def test_resolve_drops_unknown_fields() -> None:
    cfg = resolve_reflection_config(
        workspace_settings={"reflection": {"foo": "bar", "interval_iterations": 5}},
        agent_policy=None,
    )
    assert cfg.interval_iterations == 5


def test_resolve_workspace_disable_when_agent_silent() -> None:
    """Workspace ``enabled=False`` with no agent override → disabled."""
    cfg = resolve_reflection_config(
        workspace_settings={"reflection": {"enabled": False}},
        agent_policy={},
    )
    assert cfg.enabled is False


# ─── Workspace-as-killswitch (AND of workspace + agent flags) ────
def test_workspace_disabled_overrides_agent_enabled() -> None:
    """Workspace ``enabled=False`` is a hard kill switch — even an agent that
    opts back in stays disabled. ``should_reflect()`` then never injects."""
    cfg = resolve_reflection_config(
        workspace_settings={"reflection": {"enabled": False}},
        agent_policy={"reflection": {"enabled": True}},
    )
    assert cfg.enabled is False

    state = _state(cfg)
    for _ in range(20):
        state.tick_iteration()
        state.tick_tool_call()
        decision = state.should_reflect()
        assert decision.should_inject is False
        assert decision.reason == "skip:disabled"


def test_agent_disabled_overrides_workspace_enabled() -> None:
    """Agent ``enabled=False`` always wins for that agent; the workspace
    keeping reflection on for siblings doesn't force this agent on."""
    cfg = resolve_reflection_config(
        workspace_settings={"reflection": {"enabled": True}},
        agent_policy={"reflection": {"enabled": False}},
    )
    assert cfg.enabled is False


def test_both_enabled_unblocks_periodic_trigger() -> None:
    """Workspace + agent both ``enabled=True`` and the configured iteration
    interval must still drive a PERIODIC injection."""
    cfg = resolve_reflection_config(
        workspace_settings={"reflection": {"enabled": True, "interval_iterations": 8}},
        agent_policy={"reflection": {"enabled": True}},
    )
    assert cfg.enabled is True
    assert cfg.interval_iterations == 8

    state = _state(cfg)
    for _ in range(7):
        state.tick_iteration()
        assert state.should_reflect().should_inject is False
    state.tick_iteration()
    decision = state.should_reflect()
    assert decision.should_inject is True
    assert decision.kind == ReflectionKind.PERIODIC
