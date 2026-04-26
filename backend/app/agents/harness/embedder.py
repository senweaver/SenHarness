"""Pluggable embedding provider.

Resolution order:
  1. ``OPENAI_API_KEY`` (or compatible base_url) → ``text-embedding-3-small`` (1536-d,
     truncated to 1024).
  2. ``OLLAMA_HOST``                              → ``nomic-embed-text`` (768-d,
     zero-padded to 1024).
  3. **Hash fallback**                            — deterministic, zero-cost,
     suitable for unit tests and first-run demos. Quality is obviously poor but
     the system still stores/retrieves memories.

All backends return ``list[float]`` of length ``MEMORY_VECTOR_DIM`` (1024).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import httpx

from app.db.models.memory import MEMORY_VECTOR_DIM

log = logging.getLogger(__name__)


async def embed(text: str) -> tuple[list[float], str]:
    """Return ``(vector, model_tag)`` — never raises; falls through to hash."""
    text = (text or "").strip()
    if not text:
        return [0.0] * MEMORY_VECTOR_DIM, "empty"

    for fn in (_openai_embed, _ollama_embed):
        try:
            v, tag = await fn(text)
            if v is not None:
                return _fit(v), tag
        except Exception as e:  # pragma: no cover
            log.debug("embedding backend %s failed: %s", fn.__name__, e)

    return _hash_embed(text), "hash-fallback"


# ─── Backends ─────────────────────────────────────────────
async def _openai_embed(text: str) -> tuple[list[float] | None, str]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None, ""
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": text},
        )
    r.raise_for_status()
    data = r.json()
    vec: list[float] = data["data"][0]["embedding"]
    return vec, f"openai:{model}"


async def _ollama_embed(text: str) -> tuple[list[float] | None, str]:
    host = os.environ.get("OLLAMA_HOST")
    if not host:
        return None, ""
    base = host.rstrip("/")
    model = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    async with httpx.AsyncClient(timeout=30.0) as cli:
        r = await cli.post(f"{base}/api/embeddings", json={"model": model, "prompt": text})
    r.raise_for_status()
    data: dict[str, Any] = r.json()
    vec: list[float] | None = data.get("embedding")
    if vec is None:
        return None, ""
    return vec, f"ollama:{model}"


def _hash_embed(text: str) -> list[float]:
    """Deterministic pseudo-embedding from sha512 hashes. Only for dev."""
    vec: list[float] = []
    counter = 0
    seed = text.encode("utf-8", errors="replace")
    while len(vec) < MEMORY_VECTOR_DIM:
        digest = hashlib.sha512(seed + counter.to_bytes(4, "little")).digest()
        for i in range(0, len(digest), 2):
            if len(vec) >= MEMORY_VECTOR_DIM:
                break
            val = int.from_bytes(digest[i : i + 2], "little", signed=False)
            # Map to [-1, 1]
            vec.append((val / 32768.0) - 1.0)
        counter += 1
    # Normalize to unit length so cosine similarity is well-behaved.
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


# ─── Dimension fit (truncate or zero-pad) ─────────────────
def _fit(vec: list[float]) -> list[float]:
    n = len(vec)
    if n == MEMORY_VECTOR_DIM:
        return vec
    if n > MEMORY_VECTOR_DIM:
        return vec[:MEMORY_VECTOR_DIM]
    return vec + [0.0] * (MEMORY_VECTOR_DIM - n)
