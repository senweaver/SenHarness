"""Multimodal output tools — image generation, text-to-speech, speech-to-text.

All three hit an OpenAI-compatible endpoint:

* ``generate_image`` → ``POST /v1/images/generations``
* ``speak`` (TTS)    → ``POST /v1/audio/speech``
* ``transcribe`` (STT) → ``POST /v1/audio/transcriptions``

Result bytes are persisted as an ``Attachment`` scoped to the running session,
so the UI renders the image / plays the audio inline via the existing
`AttachmentView`. The tool returns the attachment id + filename so the agent
can reference them in its reply (e.g. *"Done — see attachment abc…"*).

Resolution of credentials goes through the workspace's enabled
OpenAI-compatible providers (vault-backed). No env-var fallback — operators
must configure providers via the Settings → Providers UI.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.agents.tools._context import get_context
from app.db.session import get_session_factory

log = logging.getLogger(__name__)

_DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_DEFAULT_IMAGE_MODEL = "gpt-image-1"
_DEFAULT_TTS_MODEL = "tts-1"
_DEFAULT_TTS_VOICE = "alloy"
_DEFAULT_STT_MODEL = "whisper-1"
# Cap output bytes so a runaway model can't fill the disk.
_MAX_RESULT_BYTES = 20 * 1024 * 1024


@dataclass(slots=True)
class _OpenAIish:
    api_key: str
    base_url: str


async def _resolve_openai() -> _OpenAIish | None:
    """Find the first enabled OpenAI-compatible provider for the current workspace.

    Returns ``None`` when the workspace has no provider configured — the
    caller surfaces a ``no_openai_provider`` error to the agent so the user
    knows to head to Settings → Providers.
    """
    ctx = get_context()
    ws_id = ctx.workspace_id
    try:
        from sqlalchemy import select

        from app.db.models.model_provider import ModelKey, ModelProvider
        from app.db.models.vault import VaultItem
        from app.services.vault import reveal_secret
    except ImportError:  # pragma: no cover
        return None

    # Any OpenAI-protocol provider exposes ``/v1/images`` and ``/v1/audio``.
    openai_compatible_kinds = (
        "openai",
        "azure_openai",
        "openrouter",
        "vllm",
        "custom",
    )

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ModelProvider, VaultItem)
            .join(ModelKey, ModelKey.provider_id == ModelProvider.id)
            .join(VaultItem, VaultItem.id == ModelKey.vault_item_id)
            .where(
                ModelProvider.workspace_id == ws_id,
                ModelProvider.enabled.is_(True),
                ModelProvider.deleted_at.is_(None),
                ModelKey.enabled.is_(True),
                ModelProvider.kind.in_(openai_compatible_kinds),
            )
            .order_by(ModelProvider.created_at.asc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        provider, vault_item = row
        try:
            api_key = await reveal_secret(vault_item)
        except Exception:  # pragma: no cover
            return None
    return _OpenAIish(
        api_key=api_key,
        base_url=(provider.base_url or _DEFAULT_OPENAI_BASE).rstrip("/"),
    )


async def _save_as_attachment(
    *,
    filename: str,
    mime: str,
    data: bytes,
) -> dict[str, Any]:
    """Persist ``data`` as an attachment tied to the running session.

    Returns a small dict the tool can return to the agent — id, filename,
    byte size. The UI picks these up via the session's attachments list.
    """
    if len(data) > _MAX_RESULT_BYTES:
        raise ValueError(f"result > {_MAX_RESULT_BYTES // (1024 * 1024)}MB — refusing to store")

    from app.services import attachment as att_svc

    ctx = get_context()
    factory = get_session_factory()
    async with factory() as db:
        att = await att_svc.store_bytes(
            db,
            workspace_id=ctx.workspace_id,
            uploader_identity_id=ctx.identity_id,
            filename=filename,
            mime_type=mime,
            data=data,
            session_id=ctx.session_id,
        )
        await db.commit()
        return {
            "attachment_id": str(att.id),
            "filename": att.filename,
            "mime_type": att.mime_type,
            "size_bytes": att.size_bytes,
        }


# ─── Image generation ────────────────────────────────────
class GenerateImageArgs(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000, description="What to draw.")
    size: Literal["1024x1024", "1024x1792", "1792x1024"] = Field(
        default="1024x1024", description="Output resolution."
    )
    model: str = Field(
        default=_DEFAULT_IMAGE_MODEL,
        description="Model name; defaults to gpt-image-1 (OpenAI).",
    )


async def run_generate_image(args: GenerateImageArgs) -> dict:
    provider = await _resolve_openai()
    if provider is None:
        return {
            "ok": False,
            "error": (
                "no_openai_provider: add an enabled OpenAI-compatible provider "
                "in the workspace settings (or set OPENAI_API_KEY)"
            ),
        }
    url = f"{provider.base_url}/images/generations"
    body = {
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
        "n": 1,
        "response_format": "b64_json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                url,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=body,
            )
            if r.status_code >= 300:
                return {
                    "ok": False,
                    "error": f"http_{r.status_code}",
                    "detail": r.text[:400],
                }
            j = r.json() or {}
            data = (j.get("data") or [{}])[0]
            b64 = data.get("b64_json")
            if not b64:
                # Some gateways return a URL instead of b64 — follow once.
                remote_url = data.get("url")
                if not remote_url:
                    return {"ok": False, "error": "empty_result"}
                r2 = await c.get(remote_url)
                r2.raise_for_status()
                raw = r2.content
            else:
                raw = base64.b64decode(b64)
    except httpx.HTTPError as e:
        return {"ok": False, "error": "http_error", "detail": str(e)[:400]}

    att = await _save_as_attachment(
        filename=f"generated-{uuid.uuid4().hex[:8]}.png",
        mime="image/png",
        data=raw,
    )
    return {"ok": True, **att}


# ─── TTS ─────────────────────────────────────────────────
class SpeakArgs(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, description="Text to synthesize.")
    voice: str = Field(
        default=_DEFAULT_TTS_VOICE,
        description="Voice identifier; see your provider's voice list.",
    )
    model: str = Field(default=_DEFAULT_TTS_MODEL)
    format: Literal["mp3", "wav", "opus", "aac", "flac"] = Field(default="mp3")


async def run_speak(args: SpeakArgs) -> dict:
    provider = await _resolve_openai()
    if provider is None:
        return {"ok": False, "error": "no_openai_provider"}
    url = f"{provider.base_url}/audio/speech"
    body = {
        "model": args.model,
        "input": args.text,
        "voice": args.voice,
        "response_format": args.format,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                url,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=body,
            )
            if r.status_code >= 300:
                return {
                    "ok": False,
                    "error": f"http_{r.status_code}",
                    "detail": r.text[:400],
                }
            raw = r.content
    except httpx.HTTPError as e:
        return {"ok": False, "error": "http_error", "detail": str(e)[:400]}

    mime_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
    }
    att = await _save_as_attachment(
        filename=f"speech-{uuid.uuid4().hex[:8]}.{args.format}",
        mime=mime_map.get(args.format, "application/octet-stream"),
        data=raw,
    )
    return {"ok": True, **att}


# ─── STT ─────────────────────────────────────────────────
class TranscribeArgs(BaseModel):
    attachment_id: uuid.UUID = Field(
        ..., description="ID of an audio attachment previously uploaded to the session."
    )
    model: str = Field(default=_DEFAULT_STT_MODEL)
    language: str | None = Field(
        default=None,
        description="ISO-639-1 code hint (optional; model can autodetect).",
    )


async def run_transcribe(args: TranscribeArgs) -> dict:
    provider = await _resolve_openai()
    if provider is None:
        return {"ok": False, "error": "no_openai_provider"}

    # Load the attachment bytes from the shared local storage.
    from app.services import attachment as att_svc

    ctx = get_context()
    factory = get_session_factory()
    async with factory() as db:
        try:
            att = await att_svc.get_for_read(
                db, attachment_id=args.attachment_id, workspace_id=ctx.workspace_id
            )
        except Exception as e:
            return {"ok": False, "error": "attachment_not_found", "detail": str(e)[:200]}
        try:
            audio_bytes = att_svc.read_bytes(att)
        except Exception as e:
            return {"ok": False, "error": "blob_missing", "detail": str(e)[:200]}
        filename = att.filename
        mime = att.mime_type or "audio/mpeg"

    url = f"{provider.base_url}/audio/transcriptions"
    data: dict[str, Any] = {"model": args.model}
    if args.language:
        data["language"] = args.language
    files = {"file": (filename, audio_bytes, mime)}
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                url,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                data=data,
                files=files,
            )
            if r.status_code >= 300:
                return {
                    "ok": False,
                    "error": f"http_{r.status_code}",
                    "detail": r.text[:400],
                }
            j = r.json() or {}
    except httpx.HTTPError as e:
        return {"ok": False, "error": "http_error", "detail": str(e)[:400]}

    return {"ok": True, "text": j.get("text", ""), "language": j.get("language")}
