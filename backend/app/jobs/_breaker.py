"""Per-workspace circuit breakers + sliding-window rate limiters for aux LLM tasks.

Both M0.1 (``score_message_alignment``) and M0.3 (``judge_session_artifact``)
need the same shape:

* a sliding failure counter that trips after N consecutive faults inside
  a window and auto-recovers after a longer cooldown;
* a sliding-window rate budget so a runaway producer can't burn the
  whole workspace's aux-call budget on retries.

Redis is the source of truth so multiple worker processes share state.
Every helper fails open (returns ``None`` / ``False`` / ``0``) when
Redis is unreachable — a downed Redis must not turn a degraded tenant
into a hard outage.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


# ─── Redis helpers ───────────────────────────────────────────
def _redis_or_none():
    try:
        from app.core.rate_limit import get_redis

        return get_redis()
    except Exception:  # pragma: no cover
        return None


# ─── Failure breaker (sliding window, auto-recover) ──────────
async def bump_failure(
    *,
    bucket: str,
    workspace_id: str,
    window_seconds: int,
    recover_seconds: int | None = None,
) -> int:
    """Bump the failure counter; return the *current* failure count.

    ``window_seconds`` is the rolling window the consecutive-failure
    count lives in. ``recover_seconds``, when supplied and larger than
    ``window_seconds``, becomes the actual TTL — that is the auto-trip
    recovery time for breakers that should hold the open state for
    longer than the failure-counting window (M0.3 wants 1h recovery
    for a 5/5min counter so a one-off blip doesn't keep the breaker
    open for the whole hour).
    """
    r = _redis_or_none()
    if r is None:
        return 0
    key = f"{bucket}:fail:{workspace_id}"
    try:
        async with r.pipeline() as pipe:
            pipe.incr(key, 1)
            pipe.expire(
                key,
                int(max(window_seconds, recover_seconds or window_seconds)),
            )
            count, _ = await pipe.execute()
        return int(count)
    except Exception:  # pragma: no cover
        return 0


async def reset_failure(*, bucket: str, workspace_id: str) -> None:
    r = _redis_or_none()
    if r is None:
        return
    try:
        await r.delete(f"{bucket}:fail:{workspace_id}")
    except Exception:  # pragma: no cover
        return


async def is_breaker_open(
    *,
    bucket: str,
    workspace_id: str,
    trip_at: int,
) -> bool:
    r = _redis_or_none()
    if r is None:
        return False
    try:
        raw = await r.get(f"{bucket}:fail:{workspace_id}")
    except Exception:  # pragma: no cover
        return False
    if raw is None:
        return False
    try:
        return int(raw) >= int(trip_at)
    except (TypeError, ValueError):
        return False


# ─── Rate budget (sliding window via ZSET) ───────────────────
async def consume_rate(
    *,
    bucket: str,
    workspace_id: str,
    limit: int,
    period_seconds: int = 60,
) -> bool:
    """Try to claim one slot in a sliding-window budget.

    Returns ``True`` when the call may proceed, ``False`` when the
    workspace has burned its budget for the current window. Uses a
    Redis ZSET (``score = now``) so eviction is exact rather than the
    fixed-window step that ``rate_limit.py`` uses for HTTP routes.
    Fails open on Redis errors.
    """
    r = _redis_or_none()
    if r is None:
        return True
    if limit <= 0:
        return True
    key = f"{bucket}:rate:{workspace_id}"
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(period_seconds * 1000)
    try:
        async with r.pipeline() as pipe:
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            _, current = await pipe.execute()
        if int(current) >= int(limit):
            return False
        async with r.pipeline() as pipe:
            pipe.zadd(key, {f"{now_ms}-{workspace_id}": now_ms})
            pipe.expire(key, int(period_seconds * 2))
            await pipe.execute()
        return True
    except Exception:  # pragma: no cover
        return True


async def current_rate_usage(
    *,
    bucket: str,
    workspace_id: str,
    period_seconds: int = 60,
) -> int:
    r = _redis_or_none()
    if r is None:
        return 0
    key = f"{bucket}:rate:{workspace_id}"
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(period_seconds * 1000)
    try:
        async with r.pipeline() as pipe:
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            _, current = await pipe.execute()
        return int(current)
    except Exception:  # pragma: no cover
        return 0
