"""Per-run context for tools that need session-scoped side effects (filesystem etc).

The kernel sets this before dispatching to the agent; tools read it directly
instead of plumbing context through every `tool_plain` signature.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolRunContext:
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    identity_id: uuid.UUID
    agent_id: uuid.UUID
    scratch_base: Path
    policy: dict[str, Any] = field(default_factory=dict)


_current: ContextVar[ToolRunContext | None] = ContextVar("senharness.tool_ctx", default=None)


def set_context(ctx: ToolRunContext | None) -> None:
    _current.set(ctx)


def get_context() -> ToolRunContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("ToolRunContext not set; tool called outside a kernel run")
    return ctx


def try_get_context() -> ToolRunContext | None:
    return _current.get()
