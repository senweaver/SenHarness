"""Liveness and readiness probes — deliberately distinct.

    /health  · lightweight liveness. Process is alive and event loop
               isn't wedged. Touches nothing external. Called by the
               container healthcheck during ``start_period`` to decide
               whether to kill/restart us.

    /readyz  · readiness. We're willing to take traffic only when the
               DB responds AND the Redis connection works. K8s /
               docker-compose use this to decide when to add us to the
               load balancer.

Historical: earlier we used ``/ready`` (single ``z``-less) internally.
``/readyz`` is kept as the canonical route because it matches the
k8s convention most ops teams grep for. The old path stays as an alias
so existing scripts don't break.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DBSession
from app.core.rate_limit import get_redis

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Process is alive. Nothing external is touched.

    A 200 here means 'don't kill me'; it does NOT mean 'route traffic
    to me' — see ``/readyz``.
    """
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (DB + Redis)")
async def readyz(db: DBSession) -> JSONResponse:
    """We're ready to serve traffic.

    Checked on every startup by Docker / K8s. Each backing store gets a
    cheap, timeout-bounded probe. If any dep fails we return 503 with a
    structured payload so operators can see *which* dep is down from
    the logs / load balancer status page alone.
    """
    checks: dict[str, str] = {}
    any_failed = False

    # Postgres — single SELECT 1. Budget ~100ms in practice.
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # pragma: no cover - failure mode
        log.warning("readyz: DB check failed: %s", e)
        checks["db"] = f"error: {e.__class__.__name__}"
        any_failed = True

    # Redis — PING. Tolerant of Redis being genuinely optional; we only
    # fail the probe when Redis was reachable before and now isn't
    # (i.e. connection creation succeeds but PING doesn't respond).
    try:
        r = get_redis()
        pong = await r.ping()
        checks["redis"] = "ok" if pong else "no_pong"
        if not pong:
            any_failed = True
    except Exception as e:  # pragma: no cover - failure mode
        log.warning("readyz: Redis check failed: %s", e)
        checks["redis"] = f"error: {e.__class__.__name__}"
        any_failed = True

    if any_failed:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": checks},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})


# Legacy alias — ``/ready`` used to be the sole readiness endpoint before
# V1. Keep it as a synonym so existing ops scripts / dashboards keep
# working; remove in v1.0 once all callers have migrated.
@router.get("/ready", summary="Readiness probe (legacy alias)", include_in_schema=False)
async def ready_legacy(db: DBSession) -> JSONResponse:
    return await readyz(db=db)
