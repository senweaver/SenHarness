"""Wire schemas for cross-platform logical threads (M3.6)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ThreadChannelBindingRead(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    channel_id: uuid.UUID | None
    channel_name: str | None = None
    channel_kind: str | None = None
    external_user_id: str | None
    last_seen_at: datetime
    is_paired: bool

    model_config = {"from_attributes": True}


class LogicalThreadRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    agent_id: uuid.UUID
    primary_session_id: uuid.UUID
    label: str | None
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LogicalThreadDetail(LogicalThreadRead):
    """Read shape with the binding fan-out attached.

    The detail endpoint (``GET /threads/{id}``) returns this so the
    cross-platform settings page does not need a second round-trip to
    list the channels currently mapped to a thread.
    """

    bindings: list[ThreadChannelBindingRead] = Field(default_factory=list)


class LogicalThreadList(BaseModel):
    items: list[LogicalThreadRead]
    total: int


class ThreadActiveSession(BaseModel):
    """Endpoint payload for ``GET /threads/{id}/sessions/active``."""

    thread_id: uuid.UUID
    session_id: uuid.UUID
    last_activity_at: datetime


class ThreadLabelUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)


class PairingInitiateRequest(BaseModel):
    """Start a 6-digit pairing handshake.

    The caller already has one binding (the *source* — typically the
    web/CLI session they are looking at). They pick the platform side
    they want to pair with by passing ``target_channel_id`` plus the
    ``target_external_user_id`` they expect to see arrive on that
    channel; the consumer of the code must match both fields so a
    shoulder-surfed code cannot be redeemed against a different
    binding pair.
    """

    source_channel_id: uuid.UUID | None = None
    source_external_user_id: str | None = Field(default=None, max_length=200)
    target_channel_id: uuid.UUID | None = None
    target_external_user_id: str | None = Field(default=None, max_length=200)


class PairingInitiateResponse(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    expires_at: datetime
    ttl_seconds: int


class PairingConsumeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern="^[0-9]{6}$")
    channel_id: uuid.UUID | None = None
    external_user_id: str | None = Field(default=None, max_length=200)


class PairingConsumeResponse(BaseModel):
    thread_id: uuid.UUID
    primary_session_id: uuid.UUID
    bindings_paired: int
    threads_merged: int
