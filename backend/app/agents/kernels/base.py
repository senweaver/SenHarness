"""Agent Kernel abstraction.

`AgentBackend` is the contract that SenHarness uses to run any model / framework:
`pydantic-ai` native, OpenClaw remote, future engines. The router selects the
backend via `Agent.backend_kind`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


# ─── Run events (wire format for WebSocket layer) ─────────
class RunEventKind(StrEnum):
    DELTA = "delta"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_UPDATE = "approval_update"
    USAGE = "usage"
    ERROR = "error"
    FINAL = "final"


@dataclass(slots=True)
class RunEvent:
    kind: RunEventKind
    data: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {"type": self.kind.value, "data": self.data}


# ─── Run request ──────────────────────────────────────────
@dataclass(slots=True)
class RunRequest:
    """Everything the backend needs to execute one turn."""

    run_id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID
    identity_id: uuid.UUID
    user_text: str
    message_history: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    toolbox: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    iteration_budget: int = 12
    model_override: str | None = None
    # Optional callback invoked exactly once, inside the runner, on the
    # first text-delta event of the run. Used by the WS layer to log
    # ``turn.timing first_delta`` without polling. Backends are free to
    # ignore it; the WS handler treats absence as "no first-delta probe".
    on_first_delta: Callable[[], None] | None = None


# ─── Backend capabilities descriptor ──────────────────────
@dataclass(slots=True, frozen=True)
class BackendCapabilities:
    # Runtime-shape flags — consumed by the agent composition layer.
    supports_streaming: bool = True
    supports_parallel_tools: bool = True
    supports_thinking: bool = False
    supports_native_mcp: bool = False
    supports_vision: bool = False
    max_context_tokens: int | None = None
    notes: str = ""

    # Discoverability metadata — surfaced at ``GET /agents/runtimes`` and
    # in the workspace runtime picker UI. Optional so third-party
    # adapters that don't supply them still work; the UI falls back to
    # the ``backend_kind`` string when ``display_name`` is empty.
    display_name: str = ""
    description: str = ""
    docs_url: str = ""
    requires_adapter: bool = False  # true for remote backends (OpenClaw etc.)


# ─── Protocol ─────────────────────────────────────────────
@runtime_checkable
class AgentBackend(Protocol):
    """Contract each runtime implements."""

    backend_kind: str  # must match `agents.backend_kind` string column

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:  # pragma: no cover - protocol
        ...

    async def cancel(self, run_id: uuid.UUID) -> None:  # pragma: no cover - protocol
        ...

    def capabilities(self) -> BackendCapabilities: ...
