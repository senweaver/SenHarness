"""SenHarness Agent run-time deps.

Passed to ``pydantic_ai.Agent(deps_type=...)`` + ``agent.iter(deps=...)``.

Implements three protocols used by different capabilities:
  - ``SubAgentDepsProtocol`` (subagents-pydantic-ai): exposes ``subagents`` dict
    and ``clone_for_subagent()`` method.
  - ``ConsoleDeps`` (pydantic-ai-backends): exposes ``backend`` property.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SenHarnessDeps:
    """Shared run context for tools / capabilities."""

    # Core identity — always populated by the kernel runner.
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    identity_id: uuid.UUID
    agent_id: uuid.UUID

    # Required slot for subagents-pydantic-ai SubAgentDepsProtocol.
    subagents: dict[str, Any] = field(default_factory=dict)

    # Optional backend for pydantic-ai-backends ConsoleCapability. When None,
    # the console tools raise at call time — so ConsoleCapability should only
    # be attached when this is populated.
    backend: Any = None

    agent_name: str | None = None

    def clone_for_subagent(self, max_depth: int = 0) -> SenHarnessDeps:
        """Produce isolated deps for a child subagent.

        Keeps the same workspace / identity / session so tools remain scoped
        correctly, and **shares the backend** so subagents see the same
        filesystem sandbox as the parent (otherwise they'd spawn their own
        containers and lose continuity).
        """
        return SenHarnessDeps(
            run_id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            session_id=self.session_id,
            identity_id=self.identity_id,
            agent_id=self.agent_id,
            subagents={} if max_depth <= 0 else dict(self.subagents),
            backend=self.backend,
            agent_name=f"{self.agent_name or 'worker'}:child",
        )
