"""Memory DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.memory import MemoryKind, MemoryScope
from app.schemas._base import ORMModel, Timestamped


class MemoryCreate(ORMModel):
    scope: MemoryScope = MemoryScope.USER
    scope_id: uuid.UUID | None = None
    kind: MemoryKind = MemoryKind.SEMANTIC
    key: str | None = None
    content: str = Field(min_length=1)
    value_json: dict = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_seconds: int | None = None


class MemoryUpdate(ORMModel):
    content: str | None = None
    value_json: dict | None = None
    confidence: float | None = None


class MemoryRead(Timestamped):
    workspace_id: uuid.UUID
    scope: MemoryScope
    scope_id: uuid.UUID | None
    kind: MemoryKind
    key: str | None
    content: str
    value_json: dict
    confidence: float
    ttl_at: datetime | None
    embedding_model: str | None = None
    source_session_id: uuid.UUID | None = None
    author_identity_id: uuid.UUID | None = None


class RecallIn(ORMModel):
    query: str
    limit: int = Field(default=8, ge=1, le=50)
    min_score: float = Field(default=0.30, ge=0.0, le=1.0)


class RecallHit(ORMModel):
    memory: MemoryRead
    score: float
