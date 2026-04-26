"""DTOs for BackendAdapter CRUD + the OpenClaw gateway (register/poll/emit)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.backend_adapter import BackendAdapterHealth, BackendAdapterKind
from app.schemas._base import ORMModel, Timestamped


# ─── Adapter CRUD ─────────────────────────────────────────
class BackendAdapterCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    kind: BackendAdapterKind = BackendAdapterKind.OPENCLAW
    endpoint: str | None = Field(default=None, max_length=512)
    metadata_json: dict = Field(default_factory=dict)


class BackendAdapterUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    metadata_json: dict | None = None


class BackendAdapterRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    kind: BackendAdapterKind
    endpoint: str | None
    capabilities_json: dict
    health_status: BackendAdapterHealth
    last_seen_at: datetime | None
    enabled: bool
    metadata_json: dict
    created_by: uuid.UUID | None


class BackendAdapterCreated(ORMModel):
    """One-time payload returned on create / rotate-key — plaintext API key is
    ONLY surfaced here. The client must store it immediately."""

    adapter: BackendAdapterRead
    api_key: str = Field(description="Raw X-Api-Key, shown once.")


class BackendAdapterHealthReport(ORMModel):
    status: BackendAdapterHealth
    detail: str | None = None


# ─── Gateway protocol payloads ────────────────────────────
class GatewayRegisterIn(BaseModel):
    """Remote worker calls this on startup so we can stamp capabilities."""

    worker_version: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    endpoint: str | None = Field(default=None, max_length=512)


class GatewayRegisterOut(BaseModel):
    adapter_id: uuid.UUID
    adapter_name: str
    workspace_id: uuid.UUID


class GatewayPollIn(BaseModel):
    max_messages: int = Field(default=4, ge=1, le=32)
    wait_ms: int = Field(default=20000, ge=0, le=60000)


class GatewayPollMessage(BaseModel):
    """One pending request delivered to the worker."""

    run_id: uuid.UUID
    kind: str  # "run" for request rows, "cancel" for cancellation events
    session_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_at: datetime


class GatewayPollOut(BaseModel):
    messages: list[GatewayPollMessage] = Field(default_factory=list)


class GatewayEmitIn(BaseModel):
    """Worker-emitted event. `seq` guarantees deterministic ordering and
    lets the gateway deduplicate a replayed packet via a unique constraint."""

    run_id: uuid.UUID
    seq: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=32)
    data: dict[str, Any] = Field(default_factory=dict)


class GatewayEmitOut(BaseModel):
    accepted: bool
    duplicated: bool = False
    run_terminal: bool = False
