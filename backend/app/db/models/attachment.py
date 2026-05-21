"""Attachment — blobs uploaded into a workspace.

Each attachment is owned by a workspace + an uploader (identity). It's
optionally scoped to a session — once bound, only members of that session's
workspace can download.

Storage backend is selected by ``settings.STORAGE_BACKEND`` (local for dev,
s3/oss in production). The ``storage_uri`` is the path (local) or object key
(s3); the service layer turns that into bytes.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class AttachmentKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"  # pdf / docx / txt / etc.
    OTHER = "other"


class Attachment(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_workspace_created", "workspace_id", "created_at"),
        Index("ix_attachments_session", "session_id"),
        Index("ix_attachments_uploader", "uploader_identity_id"),
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploader_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    kind: Mapped[AttachmentKind] = mapped_column(
        String(16), default=AttachmentKind.OTHER, nullable=False
    )

    # Storage backend-scoped locator — local path, s3 key, ...
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Optional metadata: image dimensions, transcript for audio, etc.
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
