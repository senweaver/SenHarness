"""Per (provider_kind, model_id) health tracking for the M2.5.3 chain failover.

Two layers, both best-effort:

* In-process LRU map for fast hot-path reads (``is_in_cooldown``,
  ``get_health``) — avoids a Redis round trip on every chain attempt
  inside the runner.
* Redis ZSET-backed shared state so multiple worker processes converge
  on the same view of provider health. The runner increments the failure
  counter on every chain attempt and clears it on success; the next
  process inherits the cooldown without rebuilding the counter.

A tracked health row is keyed by ``(provider_kind, model_id)`` because a
workspace may run two upstream models from the same provider with very
different reliability characteristics (``deepseek/v3`` vs
``deepseek/coder-v2``). Failures on one must not poison the other.

Cooldown semantics
------------------

* ``record_failure`` increments ``consecutive_failures`` and, when the
  counter crosses ``cooldown_threshold``, stamps ``cooldown_until``
  ``cooldown_seconds`` in the future. The counter and the cooldown
  expire together so a recovered provider does not stay blocked by a
  stale Redis row.
* ``record_success`` clears the counter and the cooldown — a single
  successful turn is enough to bring a provider back into rotation.
* ``is_in_cooldown`` is a fast read that the chain resolver consults
  before yielding the next candidate.

All Redis errors fail open (return ``False`` / treat the provider as
healthy). A degraded Redis must never turn a recoverable provider blip
into a hard outage.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "ProviderHealth",
    "FailureKind",
    "classify_exception",
    "get_health",
    "is_in_cooldown",
    "record_failure",
    "record_success",
    "reset_cache",
]


# ─── Failure taxonomy ───────────────────────────────────────
class FailureKind:
    """Stable string tags for the failure_kind column.

    Kept as plain constants so callers can serialize them straight into
    audit metadata without an ``enum.value`` round trip.
    """

    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_5XX = "5xx"
    AUTH = "auth"
    OTHER = "other"


_RETRYABLE_KINDS = frozenset(
    {
        FailureKind.RATE_LIMIT,
        FailureKind.TIMEOUT,
        FailureKind.CONNECTION,
        FailureKind.SERVER_5XX,
    }
)


def is_retryable_failure(kind: str) -> bool:
    """Whether a failure_kind warrants chain failover.

    ``auth`` and ``other`` short-circuit the chain because the next
    provider almost certainly has the same misconfiguration (auth) or
    the same business-level rejection (other) — retrying would burn
    quota without changing the outcome.
    """
    return kind in _RETRYABLE_KINDS


@dataclass(slots=True)
class ProviderHealth:
    """Snapshot of a single (provider_kind, model_id) health row."""

    provider_kind: str
    model_id: str
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_kind: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ─── In-process LRU cache ───────────────────────────────────
_LOCAL_CACHE_MAX = 256
_local_cache: OrderedDict[tuple[str, str], ProviderHealth] = OrderedDict()


def _key(provider_kind: str, model_id: str) -> tuple[str, str]:
    return (str(provider_kind or "").strip(), str(model_id or "").strip())


def _redis_key(provider_kind: str, model_id: str) -> str:
    pk, mid = _key(provider_kind, model_id)
    return f"provider_health:{pk}:{mid}"


def _cache_put(key: tuple[str, str], value: ProviderHealth) -> None:
    _local_cache[key] = value
    _local_cache.move_to_end(key)
    while len(_local_cache) > _LOCAL_CACHE_MAX:
        _local_cache.popitem(last=False)


def _cache_get(key: tuple[str, str]) -> ProviderHealth | None:
    value = _local_cache.get(key)
    if value is None:
        return None
    _local_cache.move_to_end(key)
    return value


def reset_cache() -> None:
    """Drop the in-process cache. Tests use this to start from scratch."""
    _local_cache.clear()


# ─── Redis helpers (best-effort) ────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_redis(redis: Any | None) -> Any | None:
    """Return a usable Redis client or ``None``.

    The runner passes its own ``redis`` so unit tests can inject a fake;
    when ``None`` we fall back to the singleton from ``rate_limit.py``
    because the runner never opens its own client.
    """
    if redis is not None:
        return redis
    try:
        from app.core.rate_limit import get_redis

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


async def _read_redis(
    redis: Any, key: str
) -> dict[str, Any] | None:
    try:
        raw = await redis.hgetall(key)
    except Exception as exc:  # pragma: no cover — degraded Redis path
        log.debug("provider_health redis read failed key=%s err=%s", key, exc)
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
    redis: Any,
    key: str,
    *,
    health: ProviderHealth,
    ttl_seconds: int,
) -> None:
    payload = {
        "provider_kind": health.provider_kind,
        "model_id": health.model_id,
        "consecutive_failures": str(int(health.consecutive_failures)),
        "cooldown_until": _to_iso(health.cooldown_until) or "",
        "last_failure_at": _to_iso(health.last_failure_at) or "",
        "last_success_at": _to_iso(health.last_success_at) or "",
        "last_failure_kind": health.last_failure_kind or "",
    }
    try:
        await redis.hset(key, mapping=payload)
        if ttl_seconds > 0:
            await redis.expire(key, int(ttl_seconds))
    except Exception as exc:  # pragma: no cover — degraded Redis path
        log.debug("provider_health redis write failed key=%s err=%s", key, exc)


async def _delete_redis(redis: Any, key: str) -> None:
    try:
        await redis.delete(key)
    except Exception as exc:  # pragma: no cover — degraded Redis path
        log.debug("provider_health redis delete failed key=%s err=%s", key, exc)


def _hydrate(
    *, provider_kind: str, model_id: str, raw: dict[str, Any]
) -> ProviderHealth:
    failures = 0
    try:
        failures = int(raw.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        failures = 0
    return ProviderHealth(
        provider_kind=provider_kind,
        model_id=model_id,
        consecutive_failures=failures,
        cooldown_until=_from_iso(raw.get("cooldown_until")),
        last_failure_at=_from_iso(raw.get("last_failure_at")),
        last_success_at=_from_iso(raw.get("last_success_at")),
        last_failure_kind=(raw.get("last_failure_kind") or None) or None,
    )


# ─── Public API ─────────────────────────────────────────────
async def get_health(
    redis: Any | None,
    *,
    provider_kind: str,
    model_id: str,
) -> ProviderHealth:
    """Return the current health snapshot.

    Reads the in-process cache first; on miss falls through to Redis and
    back-fills the cache. When Redis is unreachable we return a fresh
    healthy row — a degraded cache must never block traffic.
    """
    key = _key(provider_kind, model_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    r = _resolve_redis(redis)
    raw: dict[str, Any] | None = None
    if r is not None:
        raw = await _read_redis(r, _redis_key(*key))

    if raw is None:
        snapshot = ProviderHealth(
            provider_kind=key[0],
            model_id=key[1],
        )
    else:
        snapshot = _hydrate(provider_kind=key[0], model_id=key[1], raw=raw)
    _cache_put(key, snapshot)
    return snapshot


async def is_in_cooldown(
    redis: Any | None,
    *,
    provider_kind: str,
    model_id: str,
) -> bool:
    """Cheap predicate the chain resolver consults per attempt."""
    health = await get_health(
        redis, provider_kind=provider_kind, model_id=model_id
    )
    if health.cooldown_until is None:
        return False
    if health.cooldown_until <= _now():
        # Expired — treat as recovered. We do NOT clear the row here
        # because the next ``record_success`` / ``record_failure`` will
        # rewrite it; spending a Redis write to clean a row we'll
        # immediately re-write is wasteful.
        return False
    return True


async def record_failure(
    redis: Any | None,
    *,
    provider_kind: str,
    model_id: str,
    failure_kind: str,
    cooldown_threshold: int = 3,
    cooldown_seconds: int = 300,
) -> ProviderHealth:
    """Bump the consecutive-failure counter and maybe trip the cooldown.

    Returns the updated snapshot so the caller can inspect
    ``cooldown_until`` for audit metadata in the same step. Both the
    in-process cache and Redis are written so a sibling worker sees
    the same state on the next attempt.
    """
    key = _key(provider_kind, model_id)
    current = await get_health(
        redis, provider_kind=key[0], model_id=key[1]
    )
    new_failures = int(current.consecutive_failures) + 1
    threshold = max(1, int(cooldown_threshold))
    cooldown_until = current.cooldown_until
    cooldown_just_started = False
    if new_failures >= threshold:
        new_until = _now() + _seconds(int(cooldown_seconds))
        if cooldown_until is None or cooldown_until < new_until:
            cooldown_just_started = (
                cooldown_until is None or cooldown_until < _now()
            )
            cooldown_until = new_until
    snapshot = ProviderHealth(
        provider_kind=key[0],
        model_id=key[1],
        consecutive_failures=new_failures,
        cooldown_until=cooldown_until,
        last_failure_at=_now(),
        last_success_at=current.last_success_at,
        last_failure_kind=str(failure_kind or FailureKind.OTHER),
        extras={"cooldown_just_started": cooldown_just_started},
    )
    _cache_put(key, snapshot)
    r = _resolve_redis(redis)
    if r is not None:
        ttl = max(60, int(cooldown_seconds) * 2)
        await _write_redis(r, _redis_key(*key), health=snapshot, ttl_seconds=ttl)
    return snapshot


async def record_success(
    redis: Any | None,
    *,
    provider_kind: str,
    model_id: str,
) -> ProviderHealth:
    """Clear the failure counter + cooldown. Cheap on the happy path."""
    key = _key(provider_kind, model_id)
    snapshot = ProviderHealth(
        provider_kind=key[0],
        model_id=key[1],
        consecutive_failures=0,
        cooldown_until=None,
        last_failure_at=None,
        last_success_at=_now(),
        last_failure_kind=None,
    )
    _cache_put(key, snapshot)
    r = _resolve_redis(redis)
    if r is not None:
        # Drop the row entirely — a healthy provider has no shared
        # state to preserve and a stale TTL would leak after a
        # producer restart.
        await _delete_redis(r, _redis_key(*key))
    return snapshot


# ─── Exception classifier ───────────────────────────────────
_TIMEOUT_HINTS = ("timeout", "timed out", "deadline exceeded")
_CONNECTION_HINTS = (
    "connection",
    "connect",
    "network",
    "dns",
    "ssl",
    "certificate",
    "broken pipe",
    "reset by peer",
)
_RATE_LIMIT_HINTS = ("rate limit", "rate-limit", "rate_limit", "too many requests")
_AUTH_HINTS = (
    "unauthor",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "authentication",
)
_5XX_HINTS = (
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "502",
    "503",
    "504",
)


def classify_exception(exc: BaseException) -> str:
    """Best-effort mapping from a thrown exception → :class:`FailureKind`.

    The pydantic-ai ecosystem surfaces provider errors as either
    ``httpx`` errors (``TimeoutException`` / ``ConnectError`` /
    ``HTTPStatusError``) or vendor SDK exceptions
    (``openai.RateLimitError``, ``anthropic.APIStatusError``, ...).
    Rather than couple to every SDK we sniff:

    1. The class name (which usually carries the failure shape).
    2. The string representation (for status codes / text hints).

    Anything we can't recognise falls back to :data:`FailureKind.OTHER`,
    which is **not** retryable — the chain shouldn't burn quota on a
    failure mode where retrying is unlikely to help.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if any(h in name for h in ("timeout", "timeoutexception", "deadline")):
        return FailureKind.TIMEOUT
    if any(h in text for h in _TIMEOUT_HINTS):
        return FailureKind.TIMEOUT

    if any(h in name for h in ("ratelimit", "throttle")):
        return FailureKind.RATE_LIMIT
    if any(h in text for h in _RATE_LIMIT_HINTS) or "429" in text:
        return FailureKind.RATE_LIMIT

    if any(h in name for h in ("connect", "connection", "network", "dns", "ssl")):
        return FailureKind.CONNECTION
    if any(h in text for h in _CONNECTION_HINTS):
        return FailureKind.CONNECTION

    if any(h in name for h in ("auth", "permission", "forbidden")):
        return FailureKind.AUTH
    if any(h in text for h in _AUTH_HINTS) or "401" in text or "403" in text:
        return FailureKind.AUTH

    if any(h in text for h in _5XX_HINTS):
        return FailureKind.SERVER_5XX

    # ``HTTPStatusError`` from httpx puts the response on ``.response``.
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429:
            return FailureKind.RATE_LIMIT
        if status_code in (401, 403):
            return FailureKind.AUTH
        if 500 <= status_code < 600:
            return FailureKind.SERVER_5XX

    return FailureKind.OTHER


# ─── Internal helpers ───────────────────────────────────────
def _seconds(n: int) -> Any:
    """Tiny wrapper that returns a ``timedelta`` without importing it
    everywhere — keeps the call sites readable above.
    """
    from datetime import timedelta

    return timedelta(seconds=max(0, int(n)))


_ = time  # reserved for monotonic helpers (M2.5.3 follow-ups)
