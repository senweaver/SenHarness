"""RAG knowledge base — collections, documents, chunks with embeddings."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin

# Shared embedding dimension — matches MEMORY_VECTOR_DIM so we can use the
# same embedder. If you plug in a 1536-dim OpenAI embedder, bump both.
KNOWLEDGE_VECTOR_DIM = 1024


class DocSourceKind(StrEnum):
    TEXT = "text"  # user-pasted raw text
    URL = "url"  # fetched via trafilatura
    FILE = "file"  # uploaded file (P2)


class DocStatus(StrEnum):
    PENDING = "pending"
    INGESTING = "ingesting"
    READY = "ready"
    FAILED = "failed"


class KnowledgeCollection(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "knowledge_collections"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Tunables: {"chunk_size": 800, "chunk_overlap": 80, "embed_model": "..."}
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class KnowledgeDoc(UuidPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "knowledge_docs"
    __table_args__ = (Index("ix_knowledge_docs_collection", "collection_id"),)

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_kind: Mapped[DocSourceKind] = mapped_column(
        String(16), default=DocSourceKind.TEXT, nullable=False
    )
    source_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[DocStatus] = mapped_column(
        String(16), default=DocStatus.PENDING, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class KnowledgeChunk(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_doc_ord", "doc_id", "ord"),)

    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(KNOWLEDGE_VECTOR_DIM), nullable=True
    )
    embed_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
