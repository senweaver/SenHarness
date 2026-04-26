"""Build a ready-to-run Agent from a DB `Agent` row.

P0 produces a thin descriptor; P1 composes `pydantic_ai.Agent(..., toolsets=...)`
with capabilities (CodeMode, skills, subagents) wired in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.kernels.base import AgentBackend
from app.agents.kernels.registry import get_backend
from app.agents.prompts import assemble_system
from app.db.models.agent import Agent


@dataclass(slots=True)
class AgentRuntimeSpec:
    agent_id: str
    name: str
    backend: AgentBackend
    system_prompt: str
    toolbox: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


def build_spec(agent: Agent) -> AgentRuntimeSpec:
    backend = get_backend(str(agent.backend_kind))
    if backend is None:
        raise RuntimeError(
            f"No Agent Runtime registered for backend_kind={agent.backend_kind!r}. "
            "Ensure the backend module is imported on startup."
        )
    return AgentRuntimeSpec(
        agent_id=str(agent.id),
        name=agent.name,
        backend=backend,
        system_prompt=assemble_system(agent.persona_md),
        toolbox=[],
        skills=[str(s) for s in (agent.skill_refs_json or [])],
    )
