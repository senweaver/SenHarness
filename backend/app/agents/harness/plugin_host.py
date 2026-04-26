"""Harness plugin host — sits on top of `pydantic-ai-middleware` (when available)
to expose lifecycle hooks equivalent to hermes-agent's 11 plugin hooks.

P0: in-process registry only. P2 fully wires each hook into the pydantic-ai
backend through `pydantic-ai-middleware`.

Hook catalogue:
    pre_llm_call, post_llm_call
    pre_tool_call, post_tool_call
    transform_terminal_output, transform_tool_result
    pre_api_request, post_api_request
    on_session_start, on_session_end, on_session_reset, on_session_finalize
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

HookName = str
Hook = Callable[..., Awaitable[Any]]

_hooks: dict[HookName, list[Hook]] = defaultdict(list)

VALID_HOOKS = frozenset(
    {
        "pre_llm_call",
        "post_llm_call",
        "pre_tool_call",
        "post_tool_call",
        "transform_terminal_output",
        "transform_tool_result",
        "pre_api_request",
        "post_api_request",
        "on_session_start",
        "on_session_end",
        "on_session_reset",
        "on_session_finalize",
    }
)


def register(name: HookName, hook: Hook) -> None:
    if name not in VALID_HOOKS:
        raise ValueError(f"Unknown hook: {name!r}; valid={sorted(VALID_HOOKS)}")
    _hooks[name].append(hook)


async def fire(name: HookName, /, **payload: Any) -> Any:
    """Run all registered hooks for `name` sequentially. Last non-None return wins."""
    if name not in VALID_HOOKS:
        raise ValueError(f"Unknown hook: {name!r}")
    result: Any = None
    for hook in _hooks.get(name, ()):
        value = await hook(**payload)
        if value is not None:
            result = value
    return result


def clear(name: HookName | None = None) -> None:
    """For tests."""
    if name is None:
        _hooks.clear()
    else:
        _hooks.pop(name, None)
