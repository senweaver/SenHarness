"""Adaptive cache-marker disable window (M2.5.9).

Watches the per-(workspace, provider_kind) hit/miss stream and trips
a short circuit when a provider has consistently failed to honour
our cache markers — typically because the upstream prefix shifted, a
beta header was missed, or the workspace's prompt has drifted off
the previously cached state. After
:data:`ADAPTIVE_DISABLE_THRESHOLD` consecutive misses we disable
annotation for :data:`ADAPTIVE_DISABLE_DURATION_SECONDS`, audit the
trip via ``cache.adaptive_disabled``, and emit the M0.10
``cache.adaptive_disabled`` notification so workspace admins can spot
the regression without staring at audit logs.

Storage is Redis-backed with an in-process LRU mirror so a hot loop
in the runner doesn't pay the round trip on every chat turn. Every
Redis call is wrapped in a try/except — a degraded cache must never
flip the runner into a permanent disable.

The tracker keeps the same shape as
:mod:`app.services.provider_health`: same key namespace
(``cache_adaptive:<ws>:<kind>``) and same fail-open posture so the
runner can read it from the same hot path without juggling two
storage idioms.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "ADAPTIVE_DISABLE_DURATION_SECONDS",
    "ADAPTIVE_DISABLE_THRESHOLD",
    "CacheHitStats",
    "configure_thresholds",
    "get_stats",
    "is_cache_disabled",
    "record_cache_result",
    "reset_cache",
]


# Defaults match the M2.5.9 design (5 misses → 60 s window). Operators
# can override per-workspace via the platform_settings.cache_control
# section; ``configure_thresholds`` mutates the module-level globals
# during a runner request so subsequent calls in the same process see
# the updated values without a re-import.
ADAPTIVE_DISABLE_THRESHOLD: int = 5
ADAPTIVE_DISABLE_DURATION_SECONDS: int = 60


@dataclass(slots=True)
class CacheHitStats:
    """Per (workspace_id, provider_kind) hit/miss snapshot."""

    workspace_id: uuid.UUID
    provider_kind: str
    consecutive_misses: int = 0
    total_hits: int = 0
    total_misses: int = 0
    last_hit_at: datetime | None = None
    last_miss_at: datetime | None = None
    disabled_until: datetime | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ─── In-process LRU cache (256 entries) ─────────────────────
_LOCAL_CACHE_MAX = 256
_local_cache: OrderedDict[tuple[str, str], CacheHitStats] = OrderedDict()


def _key(workspace_id: uuid.UUID, provider_kind: str) -> tuple[str, str]:
    return (str(workspace_id), str(provider_kind or "").strip().lower())


def _redis_key(workspace_id: uuid.UUID, provider_kind: str) -> str:
    ws, pk = _key(workspace_id, provider_kind)
    return f"cache_adaptive:{ws}:{pk}"


def _cache_put(key: tuple[str, str], value: CacheHitStats) -> None:
    _local_cache[key] = value
    _local_cache.move_to_end(key)
    while len(_local_cache) > _LOCAL_CACHE_MAX:
        _local_cache.popitem(last=False)


def _cache_get(key: tuple[str, str]) -> CacheHitStats | None:
    value = _local_cache.get(key)
    if value is None:
        return None
    _local_cache.move_to_end(key)
    return value


def reset_cache() -> None:
    """Drop the in-process cache. Tests use this to start clean."""
    _local_cache.clear()


def configure_thresholds(
    *,
    threshold: int | None = None,
    duration_seconds: int | None = None,
) -> None:
    """Adjust the module-level thresholds in-place.

    The runner calls this every turn from the workspace settings layer
    so an operator can dial the platform default down to 3 misses or
    raise the cooldown to 5 minutes without redeploying.
    """
    global ADAPTIVE_DISABLE_THRESHOLD, ADAPTIVE_DISABLE_DURATION_SECONDS
    if threshold is not None:
        ADAPTIVE_DISABLE_THRESHOLD = max(1, int(threshold))
    if duration_seconds is not None:
        ADAPTIVE_DISABLE_DURATION_SECONDS = max(1, int(duration_seconds))


# ─── Redis helpers (best-effort) ────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_redis(redis: Any | None) -> Any | None:
    """Return a usable Redis client or ``None``.

    The runner passes its own ``redis`` so unit tests can inject a
    fake; on ``None`` we fall back to the singleton from
    :mod:`app.core.rate_limit` because the runner never opens its own
    client. Mirrors :mod:`app.services.provider_health` so degraded
    cache paths converge on the same fail-open posture.
    """
    if redis is not None:
        return redis
    try:
        from app.core.rate_limit import get_redis  # noqa: PLC0415

        return get_redis()
    except Exception:  # pragma: no cover — degraded Redis path
        return None


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _from_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _read_redis(redis: Any, key: str) -> dict[str, Any] | None:
    try:
        raw = await redis.hgetall(key)
    except Exception as exc:  # pragma: no cover — degraded Redis path
        log.debug("cache_adaptive redis read failed key=%s err=%s", key, exc)
        return None
    if not raw:
        return None
    out: dict[str, Any] = {}
    for k, v in raw.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        out[kk] = vv
    return out


async def _write_redis(
    redis: Any, key: str, *, stats: CacheHitStats, ttl_seconds: int
) -> None:
    payload = {
        "workspace_id": str(stats.workspace_id),
        "provider_kind": stats.provider_kind,
        "consecutive_misses": str(int(stats.consecutive_misses)),
        "total_hits": str(int(stats.total_hits)),
        "total_misses": str(int(stats.total_misses)),
        "last_hit_at": _to_iso(stats.last_hit_at) or "",
        "last_miss_at": _to_iso(stats.last_miss_at) or "",
        "disabled_until": _to_iso(stats.disabled_until) or "",
    }
    try:
        await redis.hset(key, mapping=payload)
        if ttl_seconds > 0:
            await redis.expire(key, int(ttl_seconds))
    except Exception as exc:  # pragma: no cover — degraded Redis path
        log.debug("cache_adaptive redis write failed key=%s err=%s", key, exc)


def _hydrate(
    *, workspace_id: uuid.UUID, provider_kind: str, raw: dict[str, Any]
) -> CacheHitStats:
    def _to_int(name: str) -> int:
        try:
            return int(raw.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return CacheHitStats(
        workspace_id=workspace_id,
        provider_kind=provider_kind,
        consecutive_misses=_to_int("consecutive_misses"),
        total_hits=_to_int("total_hits"),
        total_misses=_to_int("total_misses"),
        last_hit_at=_from_iso(raw.get("last_hit_at")),
        last_miss_at=_from_iso(raw.get("last_miss_at")),
        disabled_until=_from_iso(raw.get("disabled_until")),
    )


# ─── Public API ─────────────────────────────────────────────
async def get_stats(
    redis: Any | None,
    *,
    workspace_id: uuid.UUID,
    provider_kind: str,
) -> CacheHitStats:
    """Return the current snapshot, hydrating from Redis on cache miss."""
    key = _key(workspace_id, provider_kind)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    r = _resolve_redis(redis)
    raw: dict[str, Any] | None = None
    if r is not None:
        raw = await _read_redis(r, _redis_key(workspace_id, provider_kind))

    if raw is None:
        snapshot = CacheHitStats(
            workspace_id=workspace_id,
            provider_kind=key[1],
        )
    else:
        snapshot = _hydrate(
            workspace_id=workspace_id,
            provider_kind=key[1],
            raw=raw,
        )
    _cache_put(key, snapshot)
    return snapshot


async def is_cache_disabled(
    redis: Any | None,
    *,
    workspace_id: uuid.UUID,
    provider_kind: str,
) -> bool:
    """Cheap predicate: True when the workspace is inside the disable
    window for this provider.

    Resolves the snapshot first, then compares ``disabled_until``
    against ``now``. Like :mod:`app.services.provider_health`'s
    cooldown check, an expired window returns False without an
    immediate Redis write — the next ``record_cache_result`` call
    rewrites the row anyway.
    """
    stats = await get_stats(
        redis, workspace_id=workspace_id, provider_kind=provider_kind
    )
    disabled_until = stats.disabled_until
    if disabled_until is None:
        return False
    return disabled_until > _now()


async def record_cache_result(
    redis: Any | None,
    *,
    workspace_id: uuid.UUID,
    provider_kind: str,
    hit: bool,
    hit_tokens: int = 0,
) -> CacheHitStats:
    """Bump counters, trip the disable window when the threshold is met.

    Returns the updated snapshot so the runner can audit the
    transition (``cache.adaptive_disabled``) without a follow-up read.
    A hit always resets the consecutive-miss counter; a miss
    increments it and stamps a disable window when the counter
    reaches :data:`ADAPTIVE_DISABLE_THRESHOLD`.
    """
    key = _key(workspace_id, provider_kind)
    current = await get_stats(
        redis, workspace_id=workspace_id, provider_kind=key[1]
    )

    now = _now()
    consecutive = 0 if hit else int(current.consecutive_misses) + 1
    total_hits = int(current.total_hits) + (1 if hit else 0)
    total_misses = int(current.total_misses) + (0 if hit else 1)
    last_hit_at = now if hit else current.last_hit_at
    last_miss_at = current.last_miss_at if hit else now
    disabled_until = current.disabled_until
    just_disabled = False
    just_recovered = False

    if hit and disabled_until is not None and disabled_until <= now:
        # Cache window expired; recovering on the first hit clears any
        # lingering disable timestamp so subsequent calls don't keep
        # surfacing the stale "still disabled" state.
        disabled_until = None
        just_recovered = True
    elif (
        not hit
        and consecutive >= max(1, int(ADAPTIVE_DISABLE_THRESHOLD))
    ):
        new_until = now + timedelta(
            seconds=max(1, int(ADAPTIVE_DISABLE_DURATION_SECONDS))
        )
        if disabled_until is None or disabled_until < now:
            just_disabled = True
        if disabled_until is None or disabled_until < new_until:
            disabled_until = new_until

    snapshot = CacheHitStats(
        workspace_id=workspace_id,
        provider_kind=key[1],
        consecutive_misses=consecutive,
        total_hits=total_hits,
        total_misses=total_misses,
        last_hit_at=last_hit_at,
        last_miss_at=last_miss_at,
        disabled_until=disabled_until,
        extras={
            "hit_tokens_last": int(hit_tokens or 0) if hit else 0,
            "just_disabled": just_disabled,
            "just_recovered": just_recovered,
        },
    )
    _cache_put(key, snapshot)
    r = _resolve_redis(redis)
    if r is not None:
        # TTL = max(2 × disable window, 1 hour). The longer floor keeps
        # historical totals readable for the admin dashboard between
        # bursts; the multiplier keeps the row alive long enough to
        # observe a recovery after the disable expires.
        ttl_seconds = max(
            3600, 2 * max(1, int(ADAPTIVE_DISABLE_DURATION_SECONDS))
        )
        await _write_redis(
            r,
            _redis_key(workspace_id, key[1]),
            stats=snapshot,
            ttl_seconds=ttl_seconds,
        )
    return snapshot
