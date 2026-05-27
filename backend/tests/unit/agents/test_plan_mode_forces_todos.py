"""``_apply_plan_mode_overrides`` — Plan-mode safety net for PlanTab.

Plan-mode turns must always populate the todos panel, even when the
agent's ``metadata_json.todos`` opted out. The helper is pure so we
can unit-test it without spinning up the whole runner graph.
"""

from __future__ import annotations

from app.agents.kernels.native.runner import _apply_plan_mode_overrides


def test_plan_true_overrides_todos_false() -> None:
    policy = {"plan": True, "todos": False}
    out = _apply_plan_mode_overrides(policy)
    assert out["todos"] is True
    assert out["plan"] is True


def test_plan_true_sets_todos_when_missing() -> None:
    policy = {"plan": True}
    out = _apply_plan_mode_overrides(policy)
    assert out["todos"] is True


def test_plan_false_leaves_todos_false_untouched() -> None:
    policy = {"plan": False, "todos": False}
    out = _apply_plan_mode_overrides(policy)
    assert out["todos"] is False
    assert out is policy or out == policy


def test_plan_missing_leaves_todos_true_untouched() -> None:
    policy = {"todos": True}
    out = _apply_plan_mode_overrides(policy)
    assert out["todos"] is True


def test_does_not_mutate_input_when_overriding() -> None:
    policy = {"plan": True, "todos": False}
    _ = _apply_plan_mode_overrides(policy)
    assert policy["todos"] is False
