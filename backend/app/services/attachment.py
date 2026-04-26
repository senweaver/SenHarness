"""Attachment service — local-filesystem storage + mime classification.

Path layout::

    {STORAGE_LOCAL_PATH}/attachments/{workspace_id}/{yyyymm}/{uuid}.{ext}

Only the local backend is wired in D15; s3/oss land in a later phase.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFound, PermissionDenied
from app.db.models.attachment import Attachment, AttachmentKind
from app.db.repository import AsyncRepository

log = logging.getLogger(__name__)

# Byte limit per single upload. Override via settings later.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MiB


def _classify(mime: str) -> AttachmentKind:
    m = (mime or "").lower()
    if m.startswith("image/"):
        return AttachmentKind.IMAGE
    if m.startswith("audio/"):
        return AttachmentKind.AUDIO
    if m.startswith("video/"):
        return AttachmentKind.VIDEO
    if m.startswith("text/") or m in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/json",
        "application/yaml",
        "application/xml",
        "text/markdown",
    }:
        return AttachmentKind.DOCUMENT
    return AttachmentKind.OTHER


def _safe_ext(filename: str, mime: str) -> str:
    # Prefer extension from filename if it's simple ascii, else derive from mime.
    dot = filename.rfind(".")
    if 0 <= dot < len(filename) - 1:
        ext = filename[dot + 1 :].lower()
        if re.fullmatch(r"[a-z0-9]{1,10}", ext):
            return ext
    guessed = mimetypes.guess_extension(mime) if mime else None
    if guessed:
        return guessed.lstrip(".")
    return "bin"


def _local_storage_root() -> Path:
    return Path(settings.STORAGE_LOCAL_PATH) / "attachments"


async def store_bytes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    uploader_identity_id: uuid.UUID | None,
    filename: str,
    mime_type: str,
    data: bytes,
    session_id: uuid.UUID | None = None,
) -> Attachment:
    """Persist bytes to disk and create the ``attachments`` row."""
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise PermissionDenied(
            "attachment_too_large",
            code="attachment.too_large",
            extras={"max_bytes": MAX_ATTACHMENT_BYTES, "got": len(data)},
        )
    # Normalize filename — strip path separators + control chars.
    safe_name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", filename or "unnamed")[:255]
    mime = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    ext = _safe_ext(safe_name, mime)
    kind = _classify(mime)

    blob_id = uuid.uuid4()
    yyyymm = datetime.now(UTC).strftime("%Y%m")
    root = _local_storage_root() / str(workspace_id) / yyyymm
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{blob_id}.{ext}"
    path.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()

    metadata: dict = {}
    if kind == AttachmentKind.IMAGE:
        # Best-effort image dimensions without Pillow (Pillow isn't a
        # dependency yet; we record None and let the UI measure on load).
        metadata["dims"] = None

    att = await AsyncRepository(session, Attachment).create(
        workspace_id=workspace_id,
        session_id=session_id,
        uploader_identity_id=uploader_identity_id,
        filename=safe_name,
        mime_type=mime,
        size_bytes=len(data),
        kind=kind,
        storage_uri=str(path),
        sha256=digest,
        metadata_json=metadata,
    )
    # Use the attachment's own id rather than blob_id for downloads — simpler
    # security model (the id is the entire authorization surface).
    return att


async def get_for_read(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Attachment:
    """Fetch an attachment, enforcing workspace isolation."""
    row = await AsyncRepository(session, Attachment).get(attachment_id)
    if (
        row is None
        or row.deleted_at is not None
        or row.workspace_id != workspace_id
    ):
        raise NotFound("attachment_not_found", code="attachment.not_found")
    return row


def read_bytes(att: Attachment) -> bytes:
    path = Path(att.storage_uri)
    if not path.exists():
        raise NotFound("attachment_blob_missing", code="attachment.blob_missing")
    return path.read_bytes()


async def soft_delete(
    session: AsyncSession,
    *,
    attachment: Attachment,
) -> None:
    await AsyncRepository(session, Attachment).soft_delete(attachment)
    # We DON'T delete the on-disk blob here — a nightly GC job will sweep
    # anything with ``deleted_at > 30 days ago``. This keeps accidental
    # deletes recoverable for a month.
