"""Simple Redis-backed value cache (JSON payloads).

Usage::

    from app.core.cache import cache_get, cache_set

    await cache_set("agents:recent:u42", data, ttl=60)
    data = await cache_get("agents:recent:u42")
"""

from __future__ import annotations

from typing import Any

import orjson

from app.core.rate_limit import get_redis  # reuse the same Redis pool


async def cache_get(key: str) -> Any | None:
    raw = await get_redis().get(key)
    if raw is None:
        return None
    return orjson.loads(raw)


async def cache_set(key: str, value: Any, *, ttl: int = 60) -> None:
    await get_redis().set(key, orjson.dumps(value), ex=ttl)


async def cache_delete(*keys: str) -> None:
    if keys:
        await get_redis().delete(*keys)
