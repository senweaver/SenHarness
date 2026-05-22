"""Agent — the core conversational unit (UI displayed as 助理/数字员工/智能体)."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class BackendKind:
    """Known Agent Runtime kinds (not an exhaustive enum).

    Historically this was a ``StrEnum`` — but that closed set broke the
    "pluggable runtime" promise: a new adapter (e.g. a future CrewAI or
    AutoGen backend registered at import time) would be rejected at the
    schema layer even though the registry and DB column accept it.

    V1 pivot: keep the commonly-used kinds as string constants here for
    IDE autocomplete + seed defaults + code readability, but the DB
    column is plain ``String(32)`` and the Pydantic schema accepts any
    non-empty identifier. The :func:`AgentBackend` registry is the
    single source of truth at runtime: if a ``backend_kind`` is supplied
    that no registered adapter claims, the run emits a descriptive
    ``kernel.backend_missing`` error event instead of the schema
    refusing to validate.
    """

    NATIVE = "native"
    OPENCLAW = "openclaw"


def is_known_backend_kind(kind: str) -> bool:
    """True if ``kind`` matches one of the bundled adapters.

    Useful for UI hints ("unknown runtime — is its module imported?")
    but should NOT be used to gate requests; use the registry directly
    via :func:`app.agents.kernels.registry.get_backend`.
    """
    return kind in {BackendKind.NATIVE, BackendKind.OPENCLAW}


class AgentVisibility(StrEnum):
    PRIVATE = "private"  # only creator
    WORKSPACE = "workspace"  # all workspace members
    PUBLIC = "public"  # marketplace


class AutonomyLevel(StrEnum):
    """L1 = chat only, L2 = tool use, L3 = destructive → requires approval."""

    L1 = "l1"
    L2 = "l2"
    L3 = "l3"


class Agent(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    persona_md: Mapped[str | None] = mapped_column(nullable=True)

    # Free-form string (see :class:`BackendKind` for the bundled constants).
    # Registered adapters are what actually gets invoked — see
    # ``kernels/registry.get_backend(kind)``.
    backend_kind: Mapped[str] = mapped_column(
        String(32), default=BackendKind.NATIVE, nullable=False
    )

    # When ``backend_kind == "openclaw"`` (or any future remote kind), this
    # must point at a ``backend_adapters`` row so the kernel knows which
    # remote worker to dispatch to. Kept nullable so native rows — the
    # vast majority — don't need the FK.
    backend_adapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backend_adapters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Soft refs — nullable FKs land in later phases.
    model_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    toolbox_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    skill_refs_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    memory_config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    quotas_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Stable client-facing name (M2.5.7). When set, ``tool_call_json``,
    # audit metadata, ``USAGE`` events and ``/v1/models`` advertise this
    # string; the actual LLM call routes through
    # ``workspace.home_config_json["providers"]["served_alias_map"]`` to
    # an upstream provider model. ``NULL`` means "no rename" — the
    # upstream model name flows through unchanged. Indexed because
    # ``/v1/models`` builds its response from a DISTINCT scan.
    served_model_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # Per-agent default model (``"provider:model"`` string). NULL falls
    # through to ``Identity.profile_json.chat_model_prefs`` for the
    # caller, then to the workspace default. Same wire shape as
    # ``RunRequest.model_override`` so the resolver can hand it
    # straight to ``parse_override``.
    default_model: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # Preferred ``search_providers.kind`` for this agent's web_search
    # calls. NULL keeps the workspace-wide priority order (existing
    # behaviour).
    default_search_provider_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)

    visibility: Mapped[AgentVisibility] = mapped_column(
        String(32), default=AgentVisibility.WORKSPACE, nullable=False
    )
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        String(16), default=AutonomyLevel.L2, nullable=False
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
