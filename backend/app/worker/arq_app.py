"""ARQ worker settings.

The worker has no real jobs wired yet (those ship in D4 — IM channels + Flows).
But arq refuses to start with zero functions, so we register a ``heartbeat``
no-op placeholder. Add real functions to ``WorkerSettings.functions`` as they
come online.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    ctx["started"] = True
    log.info("arq worker starting; registered funcs=%d", len(WorkerSettings.functions))


async def shutdown(ctx: dict) -> None:
    log.info("arq worker stopping")


async def heartbeat(ctx: dict[str, Any]) -> str:
    """No-op ping used so arq has at least one registered function.

    Can be enqueued as ``await redis.enqueue_job("heartbeat")`` for liveness
    checks; the result is a simple ISO timestamp string.
    """
    await asyncio.sleep(0)
    from datetime import datetime

    return datetime.now(UTC).isoformat()


class WorkerSettings:
    redis_settings = None  # Filled at runtime from settings.redis_url
    on_startup = startup
    on_shutdown = shutdown
    functions: list = [heartbeat]
    # Sensible defaults; override in .env if needed.
    max_jobs: int = 16
    job_timeout: int = 300

    @classmethod
    def _inject_redis(cls) -> None:
        from arq.connections import RedisSettings

        cls.redis_settings = RedisSettings.from_dsn(settings.redis_url)


WorkerSettings._inject_redis()
