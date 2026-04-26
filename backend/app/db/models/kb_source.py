"""Knowledge base source connectors + sync run tracking + document ACL (V2).

A :class:`KbSource` represents a *connector instance* (one URL, one uploaded
file, or one S3 bucket prefix) attached to a :class:`KnowledgeCollection`.
Running :class:`KbSourceSync` captures the progress of a single sync job —
we emit incremental events from here over SSE so the UI can render a live
log without pulling the whole table.

:class:`KbAccess` is the *document-level* ACL matrix: by default every
workspace member can see every doc in a collection, but admins can narrow
access down to specific identities / departments / squads. The resolver at
search time (`services.kb_source.filter_accessible_doc_ids`) composes this
with workspace membership so the Agent only retrieves chunks the caller is
allowed to see.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class KbSourceKind(StrEnum):
    """Built-in connector kinds. New kinds can be registered at runtime via
    ``services.kb_source.register_connector`` — the DB column is plain
    ``String(32)`` so forks / enterprise modules can plug in extra kinds
    without migrations."""

    URL = "url"
    FILE = "file"
    S3 = "s3"


class KbSourceStatus(StrEnum):
    IDLE = "idle"
    SYNCING = "syncing"
    READY = "ready"
    FAILED = "failed"


class KbSyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class KbAccessSubjectKind(StrEnum):
    """What kind of principal does an ACL entry point to."""

    IDENTITY = "identity"
    DEPARTMENT = "department"
    SQUAD = "squad"
    WORKSPACE = "workspace"  # shortcut: "everybody in this workspace"


class KbAccessLevel(StrEnum):
    """Semantic level of the grant. READ is "may be retrieved by the
    Agent"; MANAGE is reserved for V3 where editors can re-ingest."""

    READ = "read"
    MANAGE = "manage"


class KbSource(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "kb_sources"
    __table_args__ = (
        Index("ix_kb_sources_collection", "collection_id"),
        Index("ix_kb_sources_kind", "kind"),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[KbSourceStatus] = mapped_column(
        String(16), default=KbSourceStatus.IDLE, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class KbSourceSync(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "kb_source_syncs"
    __table_args__ = (
        Index("ix_kb_source_syncs_source", "source_id", "created_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kb_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[KbSyncStatus] = mapped_column(
        String(16), default=KbSyncStatus.QUEUED, nullable=False
    )
    docs_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rolling append-only transcript: each entry {ts, level, msg, ...}.
    events_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class KbAccess(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "kb_access"
    __table_args__ = (
        Index(
            "ix_kb_access_doc_subject",
            "doc_id",
            "subject_kind",
            "subject_id",
            unique=True,
        ),
        Index("ix_kb_access_collection", "collection_id"),
        Index("ix_kb_access_subject", "subject_kind", "subject_id"),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``doc_id`` NULL = "applies to every doc in the collection" (collection-
    # level grant). Non-null = narrow per-document override.
    doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
        nullable=True,
    )
    subject_kind: Mapped[KbAccessSubjectKind] = mapped_column(String(16), nullable=False)
    # For SUBJECT_KIND = workspace we reuse ``workspace_id`` column, but the
    # explicit subject_id column is always populated to keep queries simple.
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    level: Mapped[KbAccessLevel] = mapped_column(
        String(16), default=KbAccessLevel.READ, nullable=False
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
