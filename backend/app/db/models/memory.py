"""Memory — long-term key-value + semantic recall.

Three dimensions orthogonal to the workspace:

  ``scope`` × ``kind``
    - scope = ``user`` / ``assistant`` / ``workspace``
    - kind  = ``kv``      — exact key-value (e.g. "preferred_editor" = "vim")
              ``episodic`` — time-stamped facts ("on 2026-04-21 the user asked ...")
              ``semantic`` — free-form notes recalled by embedding similarity

All memories carry an optional ``embedding`` so semantic recall works across kinds.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class MemoryScope(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    WORKSPACE = "workspace"


class MemoryKind(StrEnum):
    KV = "kv"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


MEMORY_VECTOR_DIM = 1024


class Memory(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        # Allow multiple entries per (scope, scope_id, key) when kind differs — so
        # we can keep both a `kv` and a `semantic` note for the same key.
        UniqueConstraint(
            "workspace_id",
            "scope",
            "scope_id",
            "kind",
            "key",
            name="uq_memories_scope_kind_key",
        ),
        Index("ix_memories_scope_scope_id", "scope", "scope_id"),
    )

    scope: Mapped[MemoryScope] = mapped_column(String(16), nullable=False, index=True)
    # Null for `workspace` scope. For `user` → identity_id, `assistant` → agent_id.
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[MemoryKind] = mapped_column(String(16), nullable=False, index=True)

    key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(nullable=False)
    value_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(MEMORY_VECTOR_DIM), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)
    ttl_at: Mapped[datetime | None] = mapped_column(nullable=True)

    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
