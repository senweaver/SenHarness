"""DTOs for KB source connectors, sync runs, and ACL grants."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.kb_source import (
    KbAccessLevel,
    KbAccessSubjectKind,
    KbSourceStatus,
    KbSyncStatus,
)
from app.schemas._base import ORMModel, Timestamped


class KbSourceCreate(ORMModel):
    collection_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    config_json: dict = Field(default_factory=dict)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class KbSourceUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = Field(default=None, min_length=1, max_length=32)
    config_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None


class KbSourceRead(Timestamped):
    workspace_id: uuid.UUID
    collection_id: uuid.UUID
    name: str
    kind: str
    config_json: dict
    enabled: bool
    status: KbSourceStatus
    last_error: str | None
    last_synced_at: str | None
    doc_count: int
    metadata_json: dict
    created_by: uuid.UUID | None


class KbSyncRead(Timestamped):
    source_id: uuid.UUID
    status: KbSyncStatus
    docs_added: int
    docs_updated: int
    docs_failed: int
    chunks_total: int
    error_text: str | None
    events_json: list
    metadata_json: dict
    started_by: uuid.UUID | None


class KbAccessCreate(ORMModel):
    collection_id: uuid.UUID
    doc_id: uuid.UUID | None = None
    subject_kind: KbAccessSubjectKind
    subject_id: uuid.UUID
    level: KbAccessLevel = KbAccessLevel.READ
    metadata_json: dict = Field(default_factory=dict)


class KbAccessRead(Timestamped):
    workspace_id: uuid.UUID
    collection_id: uuid.UUID
    doc_id: uuid.UUID | None
    subject_kind: KbAccessSubjectKind
    subject_id: uuid.UUID
    level: KbAccessLevel
    metadata_json: dict
    granted_by: uuid.UUID | None


class KbConnectorInfo(ORMModel):
    """Metadata about a registered connector (returned by ``GET /kb/connectors``)."""

    kind: str
    display_name: str
    description: str
    config_schema: dict
    supports_incremental: bool
