"""``build_todo_capability`` — default-on posture.

Four input shapes:

* ``policy.todos`` missing entirely — enabled with default config.
* ``policy.todos = True`` — enabled with default config.
* ``policy.todos = False`` — explicit opt-out, returns ``None``.
* ``policy.todos = {"enable_subtasks": True}`` — enabled with subtasks.

``pydantic-ai-todo`` is an optional dependency; when it isn't
importable the helper degrades to ``None`` for every input. We treat
that as "test environment cannot exercise the enabled branches" and
skip cleanly.
"""

from __future__ import annotations

import pytest

from app.agents.harness.todos import build_todo_capability

TODO_AVAILABLE = True
try:
    import pydantic_ai_todo  # noqa: F401
except Exception:  # pragma: no cover
    TODO_AVAILABLE = False


@pytest.mark.skipif(
    not TODO_AVAILABLE,
    reason="pydantic-ai-todo not installed in this env",
)
def test_missing_todos_key_returns_default_capability() -> None:
    cap = build_todo_capability(policy={"autonomy_level": "l2"})
    assert cap is not None
    assert getattr(cap, "enable_subtasks", None) is False


@pytest.mark.skipif(
    not TODO_AVAILABLE,
    reason="pydantic-ai-todo not installed in this env",
)
def test_none_policy_returns_default_capability() -> None:
    cap = build_todo_capability(policy=None)
    assert cap is not None
    assert getattr(cap, "enable_subtasks", None) is False


@pytest.mark.skipif(
    not TODO_AVAILABLE,
    reason="pydantic-ai-todo not installed in this env",
)
def test_explicit_true_returns_default_capability() -> None:
    cap = build_todo_capability(policy={"todos": True})
    assert cap is not None
    assert getattr(cap, "enable_subtasks", None) is False


def test_explicit_false_returns_none() -> None:
    assert build_todo_capability(policy={"todos": False}) is None


@pytest.mark.skipif(
    not TODO_AVAILABLE,
    reason="pydantic-ai-todo not installed in this env",
)
def test_dict_spec_propagates_enable_subtasks() -> None:
    cap = build_todo_capability(policy={"todos": {"enable_subtasks": True}})
    assert cap is not None
    assert getattr(cap, "enable_subtasks", None) is True
