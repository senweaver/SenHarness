"""Notification DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.notification import NotificationLevel
from app.schemas._base import ORMModel, Timestamped


class NotificationCreate(ORMModel):
    recipient_identity_id: uuid.UUID
    kind: str = Field(min_length=1, max_length=64)
    level: NotificationLevel = NotificationLevel.INFO
    title: str = Field(min_length=1, max_length=200)
    body: str | None = None
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: uuid.UUID | None = None
    action_url: str | None = Field(default=None, max_length=1024)
    metadata_json: dict = Field(default_factory=dict)


class NotificationRead(Timestamped):
    workspace_id: uuid.UUID
    recipient_identity_id: uuid.UUID
    actor_identity_id: uuid.UUID | None
    kind: str
    level: NotificationLevel
    title: str
    body: str | None
    resource_type: str | None
    resource_id: uuid.UUID | None
    action_url: str | None
    metadata_json: dict
    read_at: datetime | None
