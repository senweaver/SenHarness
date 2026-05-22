"""Agent DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.db.models.agent import AgentVisibility, AutonomyLevel, BackendKind
from app.schemas._base import ORMModel, Timestamped


def _empty_to_none(value: str | None) -> str | None:
    """Coerce empty / whitespace string to ``None``.

    Pattern-validated optional fields would otherwise reject the
    cleared form input that the React form sends as ``""``.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# Agent runtime kinds are intentionally free-form strings (see
# ``BackendKind`` docstring) so community-shipped adapters can register
# without changing this schema. The regex just rejects empty / crazy
# values; the real "does this kind exist" check runs at invocation time
# against the live registry.
_BACKEND_KIND_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


# ``default_model`` accepts ``"provider:model"`` (provider in lowercase
# identifier shape, model can carry anything the upstream understands).
# Empty string is normalised to ``None`` at the service layer so a
# cleared form input behaves the same as "no preference".
_DEFAULT_MODEL_PATTERN = r"^[a-z][a-z0-9_-]*:.+$"

# ``default_search_provider_kind`` mirrors ``search_providers.kind`` —
# free-form lowercase identifier so future search backends slot in
# without a schema migration.
_SEARCH_KIND_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


class AgentCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    persona_md: str | None = None
    backend_kind: str = Field(default=BackendKind.NATIVE, pattern=_BACKEND_KIND_PATTERN)
    backend_adapter_id: uuid.UUID | None = None
    visibility: AgentVisibility = AgentVisibility.WORKSPACE
    autonomy_level: AutonomyLevel = AutonomyLevel.L2
    avatar_url: str | None = None
    skill_refs_json: list = Field(default_factory=list)
    memory_config_json: dict = Field(default_factory=dict)
    quotas_json: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)
    served_model_name: str | None = Field(default=None, max_length=120)
    default_model: str | None = Field(default=None, max_length=160, pattern=_DEFAULT_MODEL_PATTERN)
    default_search_provider_kind: str | None = Field(
        default=None, max_length=32, pattern=_SEARCH_KIND_PATTERN
    )

    _normalize_default_model = field_validator(
        "default_model", "default_search_provider_kind", mode="before"
    )(_empty_to_none)


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
    served_model_name: str | None = Field(default=None, max_length=120)
    default_model: str | None = Field(default=None, max_length=160, pattern=_DEFAULT_MODEL_PATTERN)
    default_search_provider_kind: str | None = Field(
        default=None, max_length=32, pattern=_SEARCH_KIND_PATTERN
    )

    _normalize_default_model = field_validator(
        "default_model", "default_search_provider_kind", mode="before"
    )(_empty_to_none)


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
    served_model_name: str | None = None
    default_model: str | None = None
    default_search_provider_kind: str | None = None


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
    # Flattened from ``metadata_json`` so the marketplace can render
    # category/tag chips without the frontend having to reach into the
    # JSON blob. ``None`` means the agent isn't a curated template
    # (user-published public agents).
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


class AgentCategory(ORMModel):
    """One row in the marketplace sidebar / category picker."""

    slug: str
    name_cn: str
    name_en: str
    count: int = 0


class AgentCloneIn(ORMModel):
    """Request body for POST /agents/{id}/clone."""

    name: str | None = None
