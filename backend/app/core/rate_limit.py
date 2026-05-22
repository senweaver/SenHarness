"""Redis fixed-window rate limiter with FastAPI Depends helpers.

V1 uses a fixed-window scheme (1 Redis INCR per call, expire on first set).
It can theoretically allow up to 2x the nominal rate around window boundaries,
which is acceptable for the sensitive paths we protect (login / register /
refresh / webhook ingress). V2 will swap in a Lua-based sliding window if
we see exploitation in the wild.

Usage:

    from app.core.rate_limit import rate_limit

    @router.post("/auth/login", dependencies=[Depends(rate_limit("auth_login", 5, 60))])
    async def login(...): ...

Identifiers prefer the authenticated identity id when available, falling
back to client IP. Behind a trusted proxy we read ``X-Forwarded-For`` —
this is safe only when the operator configures their reverse proxy to
strip the client-supplied header, which is the default in the shipped
``docker-compose.prod.yml`` Traefik config.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import redis.asyncio as aioredis
from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimited

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Quota:
    limit: int
    period_seconds: int


_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def check_rate_limit(
    *,
    identifier: str,
    path: str,
    quota: Quota | None = None,
) -> None:
    """Fixed-window limiter. Raises :class:`RateLimited` when exceeded.

    Fails open (silently allows the call) if Redis is unreachable — a
    broken Redis mustn't block normal traffic. A single WARNING per failure
    is logged so operators notice.
    """
    q = quota or Quota(settings.RATE_LIMIT_DEFAULT_LIMIT, settings.RATE_LIMIT_DEFAULT_PERIOD)
    window = int(time.time()) // q.period_seconds
    key = f"rl:{identifier}:{path}:{window}"
    try:
        r = get_redis()
        async with r.pipeline() as pipe:
            pipe.incr(key, 1)
            pipe.expire(key, q.period_seconds)
            count, _ = await pipe.execute()
    except Exception as e:  # pragma: no cover - fail-open
        log.warning("rate-limit Redis unavailable (%s) — allowing request", e)
        return
    if int(count) > q.limit:
        raise RateLimited(
            f"Rate limit exceeded ({q.limit}/{q.period_seconds}s)",
            code="rate_limit.exceeded",
            extras={"limit": q.limit, "period_seconds": q.period_seconds},
        )


# ─── FastAPI Depends helpers ─────────────────────────────────
def _client_identifier(request: Request) -> str:
    """Pick the best available identifier for rate-limit bucketing.

    Order:
      1. Authenticated identity id (from ``CurrentIdentityId`` dep, if the
         route declares it — we read it off ``request.state`` post-auth).
      2. Real client IP via ``X-Forwarded-For`` (first hop) when the proxy
         is trusted.
      3. Socket client IP as last resort.
    """
    ident = getattr(request.state, "identity_id", None)
    if ident:
        return f"id:{ident}"

    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if xff:
        return f"ip:{xff}"

    client = request.client
    if client and client.host:
        return f"ip:{client.host}"
    return "ip:unknown"


def rate_limit(
    bucket: str, limit: int, period_seconds: int
) -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency that enforces a per-bucket quota.

    The bucket name becomes part of the Redis key so distinct endpoints
    keep distinct counters (``auth_login`` and ``auth_register`` don't
    share one 5-per-minute budget).
    """
    quota = Quota(limit=limit, period_seconds=period_seconds)

    async def _dep(request: Request) -> None:
        await check_rate_limit(
            identifier=_client_identifier(request),
            path=bucket,
            quota=quota,
        )

    return _dep
