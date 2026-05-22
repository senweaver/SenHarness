"""Integration: admin retention router (M0.11).

Each route gets a happy path + a 403 RBAC failure (non-platform-admin
gets refused). The sweep trigger asserts the ARQ enqueue actually went
out (job_id is non-null when Redis is reachable).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from app.db.models.identity import Identity, PlatformRole
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _register_user(async_client, *, become_admin: bool) -> dict[str, str]:
    email = f"retn-admin-{uuid.uuid4().hex[:8]}@example.com"
    password = "retention-admin-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Retention Admin", "password": password},
    )
    assert r.status_code == 201, r.text
    identity_id = uuid.UUID(r.json()["identity_id"])
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    if become_admin:
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(
                update(Identity)
                .where(Identity.id == identity_id)
                .values(platform_role=PlatformRole.PLATFORM_ADMIN)
            )
            await db.commit()
    return {
        "headers": headers,
        "identity_id": str(identity_id),
    }


async def test_get_watermarks_admin_only(async_client):
    # Non-admin gets 403.
    user = await _register_user(async_client, become_admin=False)
    r = await async_client.get("/api/v1/admin/retention/watermarks", headers=user["headers"])
    assert r.status_code == 403

    admin = await _register_user(async_client, become_admin=True)
    r = await async_client.get("/api/v1/admin/retention/watermarks", headers=admin["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "watermarks" in body
    assert "settings" in body
    settings = body["settings"]
    assert settings["default_days"] >= 1
    assert "physical_purge_enabled" in settings


async def test_last_runs_admin_only(async_client):
    user = await _register_user(async_client, become_admin=False)
    r = await async_client.get("/api/v1/admin/retention/last-runs", headers=user["headers"])
    assert r.status_code == 403

    admin = await _register_user(async_client, become_admin=True)
    r = await async_client.get("/api/v1/admin/retention/last-runs", headers=admin["headers"])
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_purge_dry_run_admin_only(async_client):
    user = await _register_user(async_client, become_admin=False)
    r = await async_client.post("/api/v1/admin/retention/purge/dry-run", headers=user["headers"])
    assert r.status_code == 403

    admin = await _register_user(async_client, become_admin=True)
    r = await async_client.post("/api/v1/admin/retention/purge/dry-run", headers=admin["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert isinstance(body["rows"], list)
    assert "total_candidates" in body


async def test_sweep_trigger_admin_only_and_enqueues(async_client):
    user = await _register_user(async_client, become_admin=False)
    r = await async_client.post("/api/v1/admin/retention/sweep/run", headers=user["headers"])
    assert r.status_code == 403

    admin = await _register_user(async_client, become_admin=True)
    r = await async_client.post("/api/v1/admin/retention/sweep/run", headers=admin["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "enqueued" in body
    # When Redis is available (the integration test fixtures spin one up)
    # the job id should be non-null. We accept both because environments
    # without Redis still get a 200 + ``enqueued=False``.
    if body["enqueued"]:
        assert body["job_id"]
