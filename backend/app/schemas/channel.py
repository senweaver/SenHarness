"""Channel DTOs."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped

# V2 — ``kind`` is an open string at the API layer so community
# provider adapters can register new kinds without this schema
# refusing to validate. The registry (services/channels/__init__.py)
# rejects unknown kinds at use-time with a clear error.
_CHANNEL_KIND_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"

BindScope = Literal["agent", "workspace", "user", "squad"]
ChannelPolicy = Literal["open", "allowlist", "disabled", "pairing"]
MenuStyle = Literal["auto", "text", "buttons"]
ReplyAttribution = Literal["prefix", "identity", "off"]
GroupOverride = Literal["shared", "per_sender"]
HandoffMode = Literal["switch", "suggest"]
# ``peer`` / ``group`` / ``channel_default`` are matched today; the rest
# are reserved rungs of the most-specific-wins ladder (P1).
BindingMatchScope = Literal[
    "peer", "thread", "role", "guild", "team", "account", "group", "channel_default"
]


class ChannelHandoffRule(ORMModel):
    """One deterministic natural-language handoff rule (P1)."""

    keywords: list[str] = Field(min_length=1)
    # Target agent: alias, ``#index`` against the menu, or an agent id.
    target: str = Field(min_length=1)
    mode: HandoffMode = "switch"


class ChannelRoutingConfig(ORMModel):
    """Wire shape of ``channels.routing_config_json`` (P0 + P1 multi-agent).

    ``bind_scope=agent`` (the default, empty config) is the legacy
    "one channel ↔ one agent" behaviour. The primary/default agent is the
    existing ``channels.default_agent_id`` and doubles as the P1 "main
    agent" default entry; this blob only configures the routing *scope*
    and policy around it. ``bind_scope=squad`` (P2) points ``scope_ref_id``
    at a Squad and routes inside its member pool via the squad ROUTER.
    """

    bind_scope: BindScope = "agent"
    # workspace_id when bind_scope=workspace, squad_id when bind_scope=squad
    # (None → the channel's own workspace for the workspace scope).
    scope_ref_id: uuid.UUID | None = None
    # Narrows the resolved pool to these agent ids when set.
    allowlist_agent_ids: list[uuid.UUID] | None = None
    dm_policy: ChannelPolicy = "open"
    group_policy: ChannelPolicy = "disabled"
    menu_style: MenuStyle = "auto"
    selection_window_seconds: int = Field(default=300, ge=0, le=86_400)
    reply_attribution: ReplyAttribution = "prefix"
    # P1 — group stickiness sharing: ``shared`` (one route per group) or
    # ``per_sender`` (each sender overrides the group route for themselves).
    group_override: GroupOverride = "shared"
    # P1 — deterministic keyword handoff rules.
    handoff_rules: list[ChannelHandoffRule] = Field(default_factory=list)


class ChannelBindingCreate(ORMModel):
    """Create a layered binding rule (P1 "most-specific-wins")."""

    match_scope: BindingMatchScope
    # The peer_key / chat_id to match; None/omitted for ``channel_default``.
    match_value: str | None = Field(default=None, max_length=200)
    bind_scope: BindScope | None = None
    scope_ref_id: uuid.UUID | None = None
    target_agent_id: uuid.UUID | None = None
    allowlist_agent_ids: list[uuid.UUID] | None = None
    priority: int = Field(default=0, ge=0, le=1000)


class ChannelBindingRead(Timestamped):
    channel_id: uuid.UUID
    match_scope: str
    match_value: str | None
    bind_scope: str | None
    scope_ref_id: uuid.UUID | None
    target_agent_id: uuid.UUID | None
    allowlist_agent_ids: list[uuid.UUID] | None = Field(
        default=None, validation_alias="allowlist_agent_ids_json"
    )
    priority: int


class ChannelCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern=_CHANNEL_KIND_PATTERN)
    config_json: dict = Field(default_factory=dict)
    default_agent_id: uuid.UUID | None = None
    default_squad_id: uuid.UUID | None = None
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)
    sender_allowlist_json: dict = Field(default_factory=dict)
    routing_config_json: ChannelRoutingConfig | None = None


class ChannelUpdate(ORMModel):
    name: str | None = None
    config_json: dict | None = None
    default_agent_id: uuid.UUID | None = None
    default_squad_id: uuid.UUID | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None
    sender_allowlist_json: dict | None = None
    routing_config_json: ChannelRoutingConfig | None = None


class ChannelRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    kind: str
    inbound_token: str
    config_json: dict
    default_agent_id: uuid.UUID | None
    default_squad_id: uuid.UUID | None
    enabled: bool
    metadata_json: dict
    sender_allowlist_json: dict = Field(default_factory=dict)
    routing_config_json: dict = Field(default_factory=dict)
    created_by: uuid.UUID | None = None


class ChannelBindCodeRead(ORMModel):
    """Response of ``POST /channels/{id}/bind-codes`` — a one-time code the
    user types in chat (``/bind <code>``) to link their account.
    """

    code: str
    ttl_seconds: int


class ChannelIngressAck(ORMModel):
    accepted: bool
    session_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    reason: str | None = None
