"""Channel DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped

# V2 — ``kind`` is an open string at the API layer so community
# provider adapters can register new kinds without this schema
# refusing to validate. The registry (services/channels/__init__.py)
# rejects unknown kinds at use-time with a clear error.
_CHANNEL_KIND_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"


class ChannelCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern=_CHANNEL_KIND_PATTERN)
    config_json: dict = Field(default_factory=dict)
    default_agent_id: uuid.UUID | None = None
    default_squad_id: uuid.UUID | None = None
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)
    sender_allowlist_json: dict = Field(default_factory=dict)


class ChannelUpdate(ORMModel):
    name: str | None = None
    config_json: dict | None = None
    default_agent_id: uuid.UUID | None = None
    default_squad_id: uuid.UUID | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None
    sender_allowlist_json: dict | None = None


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
    created_by: uuid.UUID | None = None


class ChannelIngressAck(ORMModel):
    accepted: bool
    session_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    reason: str | None = None
