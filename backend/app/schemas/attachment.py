"""Attachment DTOs."""

from __future__ import annotations

import uuid

from app.db.models.attachment import AttachmentKind
from app.schemas._base import Timestamped


class AttachmentRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None
    uploader_identity_id: uuid.UUID | None
    filename: str
    mime_type: str
    size_bytes: int
    kind: AttachmentKind
    sha256: str | None
    metadata_json: dict
