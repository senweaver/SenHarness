"""Knowledge (RAG) DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.knowledge import DocSourceKind, DocStatus
from app.schemas._base import ORMModel, Timestamped


class KnowledgeCollectionCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    config_json: dict = Field(default_factory=dict)


class KnowledgeCollectionUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    config_json: dict | None = None


class KnowledgeCollectionRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    config_json: dict
    created_by: uuid.UUID | None = None


class KnowledgeCollectionCard(KnowledgeCollectionRead):
    doc_count: int = 0
    chunk_count: int = 0


class DocIngestIn(ORMModel):
    title: str = Field(min_length=1, max_length=255)
    source_kind: DocSourceKind = DocSourceKind.TEXT
    source_uri: str | None = None
    raw_text: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class AttachmentIngestIn(ORMModel):
    attachment_id: uuid.UUID
    title: str | None = Field(default=None, max_length=255)


class KnowledgeDocRead(Timestamped):
    collection_id: uuid.UUID
    title: str
    source_kind: DocSourceKind
    source_uri: str | None
    status: DocStatus
    error: str | None
    chunk_count: int
    metadata_json: dict
    created_by: uuid.UUID | None = None


class KnowledgeSearchIn(ORMModel):
    query: str = Field(min_length=1, max_length=1024)
    top_k: int = Field(default=5, ge=1, le=25)


class KnowledgeChunkHit(ORMModel):
    id: uuid.UUID
    doc_id: uuid.UUID
    doc_title: str | None
    ord: int
    text: str
    score: float
