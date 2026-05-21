"""Integration: admin workspace-quota router (M0.12).

Each route gets a happy path + 403 RBAC failure.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from app.db.models.identity import Identity, PlatformRole
from app.db.session import get_session_factory
from app.services import workspace_quota as quota_svc
from app.services.system_settings import (
    SystemSettingKey,
    WorkspaceQuotaSettings,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def _relax_rate_window() -> None:
    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(
            db,
            SystemSettingKey.WORKSPACE_QUOTA,
            WorkspaceQuotaSettings(creation_rate_per_period=100).model_dump(),
        )
        await db.commit()
    quota_svc.reset_attempt_ledger()


async def _register(async_client, *, become_admin: bool) -> dict[str, str]:
    email = f"qa-{uuid.uuid4().hex[:8]}@example.com"
    password = "admin-quota-tester-long-pass"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "QA", "password": password},
    )
    assert r.status_code == 201, r.text
    identity_id = uuid.UUID(r.json()["identity_id"])

    if become_admin:
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(
                update(Identity)
                .where(Identity.id == identity_id)
                .values(platform_role=PlatformRole.PLATFORM_ADMIN)
            )
            await db.commit()

    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return {
        "identity_id": str(identity_id),
        "headers": {"Authorization": f"Bearer {r.json()['access_token']}"},
    }


async def test_list_admin_only(async_client):
    await _relax_rate_window()
    user = await _register(async_client, become_admin=False)
    r = await async_client.get(
        "/api/v1/admin/workspace-quotas", headers=user["headers"]
    )
    assert r.status_code == 403

    admin = await _register(async_client, become_admin=True)
    r = await async_client.get(
        "/api/v1/admin/workspace-quotas", headers=admin["headers"]
    )
    assert r.status_code == 200
    body = r.json()
    assert "rows" in body
    assert "total" in body


async def test_get_single_quota_admin_only(async_client):
    await _relax_rate_window()
    user = await _register(async_client, become_admin=False)
    admin = await _register(async_client, become_admin=True)

    r = await async_client.get(
        f"/api/v1/admin/workspace-quotas/{user['identity_id']}",
        headers=user["headers"],
    )
    assert r.status_code == 403

    r = await async_client.get(
        f"/api/v1/admin/workspace-quotas/{user['identity_id']}",
        headers=admin["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["identity_id"] == user["identity_id"]
    assert body["limit"] >= 1


async def test_set_override_changes_effective_limit(async_client):
    """Acceptance 4 — admin override is observable from ``GET /me/quota``."""
    await _relax_rate_window()
    user = await _register(async_client, become_admin=False)
    admin = await _register(async_client, become_admin=True)

    # Non-admin can't write.
    r = await async_client.patch(
        f"/api/v1/admin/identities/{user['identity_id']}/workspace-quota",
        headers=user["headers"],
        json={"quota": 10},
    )
    assert r.status_code == 403

    # Admin sets override = 10.
    r = await async_client.patch(
        f"/api/v1/admin/identities/{user['identity_id']}/workspace-quota",
        headers=admin["headers"],
        json={"quota": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["workspace_quota_override"] == 10

    # User sees the bump immediately.
    r = await async_client.get(
        "/api/v1/me/workspace-quota", headers=user["headers"]
    )
    assert r.status_code == 200
    assert r.json()["limit"] == 10
    assert r.json()["override_active"] is True

    # Clearing reverts to platform default.
    r = await async_client.patch(
        f"/api/v1/admin/identities/{user['identity_id']}/workspace-quota",
        headers=admin["headers"],
        json={"quota": None},
    )
    assert r.status_code == 200
    assert r.json()["workspace_quota_override"] is None
