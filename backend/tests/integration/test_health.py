"""Integration smoke tests for /health and /readyz.

Liveness is trivial — we still pin it so a future regression that
accidentally makes /health touch the DB is caught fast. Readiness
exercises the DB + Redis probes we added in V1 week 2.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_health_returns_ok(async_client):
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_reports_each_dep(async_client):
    """``/readyz`` must list every backing store with its status so an
    ops dashboard can highlight exactly which one is down.
    """
    resp = await async_client.get("/api/v1/readyz")
    assert resp.status_code == 200  # DB and Redis are both up in tests
    body = resp.json()
    assert body["status"] == "ready"
    checks = body["checks"]
    assert checks["db"] == "ok"
    assert checks["redis"] == "ok"


async def test_ready_legacy_alias_still_works(async_client):
    """``/ready`` is kept as an alias during V1 to avoid breaking
    existing monitoring scripts."""
    resp = await async_client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
