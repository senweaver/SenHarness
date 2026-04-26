"""Agent DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.agent import AgentVisibility, AutonomyLevel, BackendKind
from app.schemas._base import ORMModel, Timestamped

# Agent runtime kinds are intentionally free-form strings (see
# ``BackendKind`` docstring) so community-shipped adapters can register
# without changing this schema. The regex just rejects empty / crazy
# values; the real "does this kind exist" check runs at invocation time
# against the live registry.
_BACKEND_KIND_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


class AgentCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    persona_md: str | None = None
    backend_kind: str = Field(
        default=BackendKind.NATIVE, pattern=_BACKEND_KIND_PATTERN
    )
    backend_adapter_id: uuid.UUID | None = None
    visibility: AgentVisibility = AgentVisibility.WORKSPACE
    autonomy_level: AutonomyLevel = AutonomyLevel.L2
    avatar_url: str | None = None
    skill_refs_json: list = Field(default_factory=list)
    memory_config_json: dict = Field(default_factory=dict)
    quotas_json: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)


class AgentUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    persona_md: str | None = None
    backend_kind: str | None = Field(default=None, pattern=_BACKEND_KIND_PATTERN)
    backend_adapter_id: uuid.UUID | None = None
    visibility: AgentVisibility | None = None
    autonomy_level: AutonomyLevel | None = None
    avatar_url: str | None = None
    skill_refs_json: list | None = None
    memory_config_json: dict | None = None
    quotas_json: dict | None = None
    metadata_json: dict | None = None


class AgentRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    persona_md: str | None
    avatar_url: str | None
    backend_kind: str
    backend_adapter_id: uuid.UUID | None = None
    visibility: AgentVisibility
    autonomy_level: AutonomyLevel
    skill_refs_json: list
    memory_config_json: dict
    quotas_json: dict
    metadata_json: dict
    created_by: uuid.UUID | None


class AgentRecent(AgentRead):
    """Agent enriched with last-use telemetry (for sidebar 'recent agents')."""

    starred: bool = False
    pinned: bool = False
    last_message_at: datetime | None = None
    message_count: int = 0


class StarAgentOut(ORMModel):
    agent_id: uuid.UUID
    starred: bool
    pinned: bool


class AgentPublicCard(AgentRead):
    """Row for the /agents/discover marketplace grid."""

    stars: int = 0


class AgentCloneIn(ORMModel):
    """Request body for POST /agents/{id}/clone."""

    name: str | None = None
