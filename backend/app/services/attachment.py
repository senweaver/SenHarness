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
from dataclasses import dataclass, field
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

# When the user attaches a document/text file, we copy the bytes into the
# session's scratch directory so the agent's filesystem tools (read_file /
# list_files / search_files) can see it, and try to extract plaintext.
# Hard cap on how many characters of extracted text we inline across all
# attachments in a single turn. Keeps token usage bounded even if the user
# attaches a stack of small files. (Bytes-on-disk are not capped beyond
# ``MAX_ATTACHMENT_BYTES`` and the extractor's own ``MAX_EXTRACT_BYTES``.)
MAX_INLINE_EXTRACT_CHARS = 24_000
# Per-file extracted excerpt length cap.
MAX_PER_FILE_EXCERPT_CHARS = 8_000


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


# ─── Session-scratch + inline-text bridge ───────────────────────────
@dataclass
class PreparedAttachment:
    """Result of materializing one chat-turn attachment.

    ``ref`` is the JSON we stash on the user message (stable across reloads).
    ``image_blob`` is set only for ``image/*`` attachments — those bytes get
    inlined into the model prompt as ``BinaryContent`` by the kernel.
    ``scratch_relpath`` is the filename inside ``scratch/<session_id>/`` for
    non-image attachments that got copied so ``list_files`` / ``read_file``
    can inspect them; ``None`` for images and for failed copies.
    ``text_relpath`` is set for binary docs (PDF / DOCX / XLSX) where we
    successfully extracted plaintext — we drop a sibling ``.txt`` so
    ``read_file`` returns something useful instead of binary garbage.
    For text/markdown attachments ``text_relpath`` equals ``scratch_relpath``.
    ``inline_excerpt`` is a short plaintext excerpt prepended to the user
    prompt for small documents — ``None`` when the file is unsupported or
    too large to extract.
    """

    ref: dict
    image_blob: tuple[str, str, bytes] | None = None
    scratch_relpath: str | None = None
    text_relpath: str | None = None
    inline_excerpt: str | None = None
    inline_truncated: bool = False


@dataclass
class PreparedAttachments:
    """Aggregate result for one user turn.

    ``refs`` goes verbatim onto the user ``Message.attachments_json``.
    ``image_blobs`` flows to ``RunRequest.attachments`` (kernel inlines them).
    ``prompt_prefix`` is concatenated *before* the user's text — empty when
    no document could be extracted.
    """

    refs: list[dict] = field(default_factory=list)
    image_blobs: list[tuple[str, str, bytes]] = field(default_factory=list)
    prompt_prefix: str = ""


def _scratch_dir_for(session_id: uuid.UUID) -> Path:
    return Path(settings.STORAGE_LOCAL_PATH) / "scratch" / str(session_id)


def _safe_scratch_filename(filename: str) -> str:
    """Sanitize a user-supplied filename so it sits flat inside scratch root.

    ``services.attachment.store_bytes`` already strips path separators + ctrl
    chars at upload time, but we belt-and-brace here in case an older row
    survived a previous, more permissive validator.
    """
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", filename or "attachment")[:120]
    return name or "attachment"


def _allocate_scratch_path(scratch_dir: Path, filename: str) -> Path:
    """Return a non-colliding path under ``scratch_dir`` for ``filename``.

    Adds ``(2)``, ``(3)``, … suffix when the name is already taken — same
    convention as a typical OS file-manager.
    """
    base = _safe_scratch_filename(filename)
    candidate = scratch_dir / base
    if not candidate.exists():
        return candidate
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    for n in range(2, 100):
        suffix = f" ({n})"
        new_name = f"{stem}{suffix}.{ext}" if ext else f"{stem}{suffix}"
        candidate = scratch_dir / new_name
        if not candidate.exists():
            return candidate
    # Extreme collision — fall back to a uuid-prefixed name.
    return scratch_dir / f"{uuid.uuid4().hex[:8]}-{base}"


def _build_prompt_prefix(prepared: list[PreparedAttachment]) -> str:
    """Assemble the ``[Attached files] / [Excerpt]`` block prepended to the prompt.

    Returns "" when there's nothing useful to surface (caller skips the
    prefix entirely in that case so we don't pollute simple chats).
    """
    listed = [p for p in prepared if p.scratch_relpath or p.inline_excerpt]
    if not listed:
        return ""

    lines: list[str] = ["[Attached files]"]
    for p in listed:
        size = p.ref.get("size_bytes")
        size_str = f" ({size} bytes)" if isinstance(size, int) else ""
        primary = p.text_relpath or p.scratch_relpath
        if primary and p.text_relpath and p.text_relpath != p.scratch_relpath:
            # Binary doc with a side-by-side extracted .txt. Point the agent
            # at the text version so ``read_file`` returns sensible content.
            lines.append(
                f"- {p.scratch_relpath}{size_str} — binary; use "
                f"`read_file('{p.text_relpath}')` for the extracted plain text."
            )
        elif primary:
            lines.append(
                f"- {primary}{size_str} — saved to session scratch; "
                f"use `read_file`/`search_files` for the full content."
            )
        else:
            lines.append(f"- {p.ref.get('filename', '?')}{size_str}")

    excerpts = [p for p in prepared if p.inline_excerpt]
    if excerpts:
        lines.append("")
        for p in excerpts:
            tail = " (truncated; use `read_file` for more)" if p.inline_truncated else ""
            name = p.text_relpath or p.scratch_relpath or p.ref.get("filename", "attachment")
            lines.append(f"--- BEGIN excerpt: {name}{tail} ---")
            lines.append(p.inline_excerpt or "")
            lines.append(f"--- END excerpt: {name} ---")
    lines.append("")
    return "\n".join(lines)


async def prepare_for_chat_turn(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    attachment_ids: list[uuid.UUID] | None,
) -> PreparedAttachments:
    """Materialize chat-turn attachments for the kernel.

    For each id we:
      1. Fetch the row + bind it to this session if needed (so cleanup later
         can find it).
      2. Always emit a ``ref`` row for ``Message.attachments_json``.
      3. For ``image/*``: load bytes for inline ``BinaryContent``.
      4. For everything else: copy bytes into the session scratch dir so
         ``list_files`` / ``read_file`` can see the file. Failures here
         downgrade to "metadata only" — they never block the turn.
      5. For documents/text small enough to extract, prepend a plain-text
         excerpt to the user's prompt so the model sees the content even
         without calling a tool.
    """
    if not attachment_ids:
        return PreparedAttachments()

    scratch_dir = _scratch_dir_for(session_id)
    try:
        scratch_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("scratch dir create failed for session %s: %s", session_id, e)

    prepared: list[PreparedAttachment] = []
    inline_chars_used = 0

    for aid in attachment_ids:
        try:
            att = await get_for_read(session, attachment_id=aid, workspace_id=workspace_id)
        except Exception:
            continue

        if att.session_id is None:
            att.session_id = session_id
            await session.flush([att])

        ref: dict = {
            "id": str(att.id),
            "filename": att.filename,
            "mime_type": att.mime_type,
            "kind": att.kind.value if hasattr(att.kind, "value") else str(att.kind),
            "size_bytes": att.size_bytes,
        }

        if att.kind == AttachmentKind.IMAGE:
            try:
                blob = read_bytes(att)
            except Exception:
                blob = None
            prepared.append(
                PreparedAttachment(
                    ref=ref,
                    image_blob=(att.kind.value, att.mime_type, blob)
                    if blob is not None
                    else None,
                )
            )
            continue

        # Non-image: copy into scratch + try to extract a short excerpt.
        try:
            data = read_bytes(att)
        except Exception as e:
            log.warning("attachment blob unreadable id=%s: %s", att.id, e)
            prepared.append(PreparedAttachment(ref=ref))
            continue

        scratch_path = _allocate_scratch_path(scratch_dir, att.filename or f"{att.id}.bin")
        try:
            scratch_path.write_bytes(data)
            scratch_rel = scratch_path.name
        except OSError as e:
            log.warning(
                "scratch copy failed id=%s -> %s: %s", att.id, scratch_path, e
            )
            scratch_rel = None
        ref["scratch_path"] = scratch_rel

        # Try to extract plaintext for documents. We feed the result two
        # places: a sibling ``.txt`` file in scratch (so ``read_file`` returns
        # useful content for binary formats like PDF/DOCX/XLSX), and an
        # inline excerpt that goes into the prompt prefix.
        extracted_text: str | None = None
        if att.kind == AttachmentKind.DOCUMENT:
            try:
                from app.services.knowledge import (
                    AttachmentExtractError,
                    extract_text_from_attachment,
                )

                extracted_text = extract_text_from_attachment(att, data)
            except AttachmentExtractError as e:
                log.info(
                    "skip extract for %s (%s): %s",
                    att.filename,
                    att.mime_type,
                    e.code,
                )
            except Exception:  # pragma: no cover - belt-and-brace
                log.exception("excerpt extraction crashed for att=%s", att.id)

        text_rel: str | None = None
        if extracted_text and scratch_rel is not None:
            mime_lower = (att.mime_type or "").lower()
            is_text_native = (
                mime_lower.startswith("text/")
                or mime_lower
                in {
                    "application/json",
                    "application/yaml",
                    "application/x-yaml",
                    "application/xml",
                    "application/javascript",
                    "application/x-sh",
                    "application/x-python-code",
                    "application/sql",
                    "application/toml",
                    "application/x-toml",
                    "text/markdown",
                }
            )
            if is_text_native:
                # Already plaintext on disk — point the agent at the same file.
                text_rel = scratch_rel
            else:
                # Binary doc: drop a sibling ``<basename>.txt`` so read_file
                # returns extracted plain text instead of raw bytes.
                stem = scratch_path.stem or "extracted"
                txt_path = _allocate_scratch_path(scratch_dir, f"{stem}.txt")
                try:
                    txt_path.write_text(extracted_text, encoding="utf-8")
                    text_rel = txt_path.name
                    ref["text_path"] = text_rel
                except OSError as e:
                    log.warning(
                        "scratch text-sidecar write failed id=%s -> %s: %s",
                        att.id,
                        txt_path,
                        e,
                    )

        # Inline excerpt: bounded by per-file + total char caps. We rely on
        # the char budget rather than ``size_bytes`` — a 2 MB PDF can extract
        # to a tiny abstract that's still worth inlining; the extractor
        # itself enforces ``MAX_EXTRACT_BYTES`` so we never decode a huge
        # blob just to throw it away.
        excerpt: str | None = None
        truncated = False
        if extracted_text and inline_chars_used < MAX_INLINE_EXTRACT_CHARS:
            budget = min(
                MAX_PER_FILE_EXCERPT_CHARS,
                MAX_INLINE_EXTRACT_CHARS - inline_chars_used,
            )
            if budget > 0:
                if len(extracted_text) > budget:
                    excerpt = extracted_text[:budget].rstrip()
                    truncated = True
                else:
                    excerpt = extracted_text.strip()
                if excerpt:
                    inline_chars_used += len(excerpt)

        prepared.append(
            PreparedAttachment(
                ref=ref,
                scratch_relpath=scratch_rel,
                text_relpath=text_rel,
                inline_excerpt=excerpt,
                inline_truncated=truncated,
            )
        )

    out = PreparedAttachments()
    for p in prepared:
        out.refs.append(p.ref)
        if p.image_blob is not None:
            out.image_blobs.append(p.image_blob)
    out.prompt_prefix = _build_prompt_prefix(prepared)
    return out
