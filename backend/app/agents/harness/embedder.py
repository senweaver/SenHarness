"""Pluggable embedding provider — catalog-driven, vault-backed.

Resolution order (per call):
  1. Workspace's first enabled provider whose ``kind`` declares an
     ``embedding`` SKU in :mod:`app.agents.kernels.model_catalog`
     (selection is delegated to ``resolve_embedder_for_workspace``).
  2. If that's an Ollama provider, the request goes to the native
     ``${base}/api/embeddings`` (different request shape).
  3. Any OpenAI-protocol embedder hits ``${base_url}/embeddings``.

If no embedding-capable provider is configured, ``embed()`` returns
``(None, "")``. Callers MUST handle that by skipping similarity-based
recall — there is no hash fallback. This keeps the agent's hot path
free of speculative 404s against chat-only providers (DeepSeek,
Moonshot, xAI, Aliyun coding plan, MiniMax, ...).

Embedding-vector dimensionality is fitted to ``MEMORY_VECTOR_DIM``
(truncate or zero-pad) so the pgvector column accepts every backend
without a migration per model.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from app.db.models.memory import MEMORY_VECTOR_DIM

log = logging.getLogger(__name__)


# Negative cache for transient backend failures (4xx / timeout). The
# canonical store is Redis (``emb:skip:{provider_id}``) so every
# worker on every host converges on the same skip window; the local
# dict is a hot-path mirror that avoids a Redis round-trip on the
# happy path. When Redis is down both layers degrade to the in-
# process dict.
_BACKEND_SKIP_TTL_SEC = 300
_BACKEND_SKIP_CACHE: dict[uuid.UUID, float] = {}


def _safe_redis_client() -> Any | None:
    """Best-effort Redis lookup; ``None`` keeps embedder degraded-but-up."""
    try:
        from app.core.rate_limit import get_redis

        return get_redis()
    except Exception:  # pragma: no cover - import / config failure
        return None


def _skip_key(provider_id: uuid.UUID) -> str:
    return f"emb:skip:{provider_id}"


async def _is_skipped(provider_id: uuid.UUID) -> bool:
    deadline = _BACKEND_SKIP_CACHE.get(provider_id)
    now = time.time()
    if deadline is not None:
        if deadline >= now:
            return True
        _BACKEND_SKIP_CACHE.pop(provider_id, None)

    redis = _safe_redis_client()
    if redis is None:
        return False
    try:
        exists = await redis.exists(_skip_key(provider_id))
    except Exception:  # pragma: no cover - redis hiccup, fail open
        return False
    if exists:
        # Mirror locally so the next hit on this worker skips the
        # Redis round-trip until the row expires.
        _BACKEND_SKIP_CACHE[provider_id] = now + _BACKEND_SKIP_TTL_SEC
        return True
    return False


async def _mark_skip(provider_id: uuid.UUID) -> None:
    _BACKEND_SKIP_CACHE[provider_id] = time.time() + _BACKEND_SKIP_TTL_SEC
    redis = _safe_redis_client()
    if redis is None:
        return
    try:
        await redis.set(_skip_key(provider_id), "1", ex=_BACKEND_SKIP_TTL_SEC)
    except Exception:  # pragma: no cover - redis hiccup, local cache still set
        return


async def embed(
    text: str, *, workspace_id: uuid.UUID | None = None
) -> tuple[list[float] | None, str]:
    """Return ``(vector, model_tag)``.

    * ``(vector, tag)`` on success — ``vector`` is fitted to
      ``MEMORY_VECTOR_DIM`` and ``tag`` is ``"{provider_kind}:{model}"``.
    * ``([0.0]*MEMORY_VECTOR_DIM, "empty")`` for empty input.
    * ``(None, "")`` when there's no embedding-capable provider, or the
      backend failed and is parked under the negative-cache TTL.

    Callers must check for ``None`` and skip similarity search / write
    ``embedding=NULL`` when present.
    """
    text = (text or "").strip()
    if not text:
        return [0.0] * MEMORY_VECTOR_DIM, "empty"
    if workspace_id is None:
        return None, ""

    from app.agents.kernels.model_client import resolve_embedder_for_workspace

    resolved = await resolve_embedder_for_workspace(workspace_id=workspace_id)
    if resolved is None:
        return None, ""

    if await _is_skipped(resolved.provider_id):
        return None, ""

    try:
        if resolved.provider_kind == "ollama":
            vec = await _http_ollama_native_embed(resolved, text)
        else:
            vec = await _http_openai_compatible_embed(resolved, text)
    except httpx.HTTPStatusError as exc:
        await _mark_skip(resolved.provider_id)
        log.info(
            "embed http error provider=%s model=%s status=%s",
            resolved.provider_kind,
            resolved.embedding_model,
            exc.response.status_code,
        )
        return None, ""
    except Exception as exc:  # pragma: no cover — transient network / parse
        await _mark_skip(resolved.provider_id)
        log.warning(
            "embed backend failed provider=%s model=%s err=%s",
            resolved.provider_kind,
            resolved.embedding_model,
            type(exc).__name__,
        )
        return None, ""

    if vec is None:
        await _mark_skip(resolved.provider_id)
        return None, ""
    tag = f"{resolved.provider_kind}:{resolved.embedding_model}"
    return _fit(vec), tag


# ─── HTTP backends ────────────────────────────────────────
async def _http_openai_compatible_embed(resolved: Any, text: str) -> list[float] | None:
    """POST ``${base_url}/embeddings`` (OpenAI / Azure / DashScope / Zhipu /
    SiliconFlow). Returns ``None`` on missing payload; raises on 4xx/5xx
    so the caller's negative cache kicks in."""
    if resolved.api_key is None:
        return None
    base = (resolved.base_url or "https://api.openai.com/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {resolved.api_key}"},
            json={"model": resolved.embedding_model, "input": text},
        )
    r.raise_for_status()
    data = r.json()
    items = data.get("data") or []
    if not items:
        return None
    vec = items[0].get("embedding")
    if not isinstance(vec, list):
        return None
    return vec


async def _http_ollama_native_embed(resolved: Any, text: str) -> list[float] | None:
    """Ollama's native ``/api/embeddings`` returns ``{"embedding": [...]}``.

    The compat shim at ``/v1`` is OpenAI-shaped but undocumented; we
    strip it and target the native path so the response shape is
    stable across Ollama versions.
    """
    base = (resolved.base_url or "http://localhost:11434/v1").rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.post(
            f"{base}/api/embeddings",
            json={"model": resolved.embedding_model, "prompt": text},
        )
    r.raise_for_status()
    payload: dict[str, Any] = r.json()
    vec = payload.get("embedding")
    if not isinstance(vec, list):
        return None
    return vec


# ─── Dimension fit (truncate or zero-pad) ─────────────────
def _fit(vec: list[float]) -> list[float]:
    n = len(vec)
    if n == MEMORY_VECTOR_DIM:
        return vec
    if n > MEMORY_VECTOR_DIM:
        return vec[:MEMORY_VECTOR_DIM]
    return vec + [0.0] * (MEMORY_VECTOR_DIM - n)
