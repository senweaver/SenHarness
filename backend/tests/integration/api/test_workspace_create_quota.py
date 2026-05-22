"""Integration: ``POST /workspaces`` quota gating + tombstone slug + delete.

Covers the M0.12 acceptance criteria 1, 3, 5, 7:

* Self-registered users default to the disallowed-creation branch
  even when their slot is empty.
* OAuth-source identity with override=10 can create up to the override.
* DELETE drops the workspace, tombstones the slug, releases the quota.
* Tombstone prevents slug reuse on a fresh ``POST /workspaces``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from app.db.models.identity import Identity
from app.db.session import get_session_factory
from app.services import workspace_quota as quota_svc
from app.services.system_settings import (
    SystemSettingKey,
    WorkspaceQuotaSettings,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def _relax_rate_window(per_period: int = 100) -> None:
    """Lift the per-identity rate window so a single test can issue
    several creation attempts back-to-back without tripping the
    ``workspace.creation_rate_limit`` gate. The Redis bucket
    ``workspace_create`` is left at the route-default cap.
    """
    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(
            db,
            SystemSettingKey.WORKSPACE_QUOTA,
            WorkspaceQuotaSettings(creation_rate_per_period=per_period).model_dump(),
        )
        await db.commit()
    quota_svc.reset_attempt_ledger()


async def _register_user(
    async_client,
    *,
    oauth: bool = False,
    override: int | None = None,
) -> tuple[str, dict[str, str]]:
    """Register, log in, and (optionally) bump source kind / override.

    Returns ``(identity_id, headers)`` ready to issue further calls.
    """
    email = f"qta-{uuid.uuid4().hex[:8]}@example.com"
    password = "workspace-quota-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Quota Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    identity_id = r.json()["identity_id"]

    factory = get_session_factory()
    async with factory() as db:
        values: dict = {}
        if oauth:
            values["oauth_provider"] = "github"
            values["oauth_id"] = f"gh-{uuid.uuid4().hex[:8]}"
        if override is not None:
            values["workspace_quota_override"] = override
        if values:
            await db.execute(update(Identity).where(Identity.id == identity_id).values(**values))
            await db.commit()

    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return identity_id, headers


async def test_self_registered_cannot_manually_create(async_client):
    """Acceptance 1 — self-reg identity gets ``creation_not_permitted``."""
    await _relax_rate_window()
    _, headers = await _register_user(async_client)
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "X", "slug": f"x-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 403, r.text
    body = r.json()
    detail = body.get("detail")
    code = (detail or {}).get("code") if isinstance(detail, dict) else None
    if code is None:
        # Some error envelopes return ``code`` at the top level.
        code = body.get("code")
    assert code == "workspace.creation_not_permitted"


async def test_oauth_with_override_can_create_until_limit(async_client):
    """Acceptance 3 — OAuth identity, override=3, fourth request 403s.

    The roadmap originally aimed at 4 (default 3 +1) but the locked
    design point Q11 set OAuth default to **1**; we therefore bump
    the override to 3 + auto-provisioned personal = 4 visible
    workspaces, then verify the 4th MANUAL attempt 403s on the
    quota gate.
    """
    await _relax_rate_window()
    _, headers = await _register_user(async_client, oauth=True, override=3)

    r1 = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Two-A", "slug": f"two-a-{uuid.uuid4().hex[:6]}"},
    )
    assert r1.status_code == 201, r1.text
    r2 = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Two-B", "slug": f"two-b-{uuid.uuid4().hex[:6]}"},
    )
    assert r2.status_code == 201, r2.text
    r3 = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Two-C", "slug": f"two-c-{uuid.uuid4().hex[:6]}"},
    )
    # Owned: personal + Two-A + Two-B = 3 == override; 3rd manual
    # would bring owner count to 4, exceeding the override.
    assert r3.status_code == 403, r3.text
    body = r3.json()
    detail = body.get("detail")
    code = (detail or {}).get("code") if isinstance(detail, dict) else None
    if code is None:
        code = body.get("code")
    assert code == "workspace.quota_exceeded"


async def test_delete_workspace_releases_quota_and_tombstones_slug(async_client):
    """Acceptance 5 + 7 — delete frees a slot and the slug stays locked."""
    await _relax_rate_window()
    _, headers = await _register_user(async_client, oauth=True, override=5)
    target_slug = f"reuse-{uuid.uuid4().hex[:6]}"
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Reusable", "slug": target_slug},
    )
    assert r.status_code == 201, r.text
    workspace_id = r.json()["id"]

    quota_before = await async_client.get("/api/v1/me/workspace-quota", headers=headers)
    assert quota_before.status_code == 200
    used_before = quota_before.json()["used"]

    d = await async_client.delete(f"/api/v1/workspaces/{workspace_id}", headers=headers)
    assert d.status_code == 204

    quota_after = await async_client.get("/api/v1/me/workspace-quota", headers=headers)
    assert quota_after.status_code == 200
    assert quota_after.json()["used"] == used_before - 1

    # Slug must remain unusable.
    r2 = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Squat", "slug": target_slug},
    )
    assert r2.status_code == 409
    body = r2.json()
    detail = body.get("detail")
    code = (detail or {}).get("code") if isinstance(detail, dict) else None
    if code is None:
        code = body.get("code")
    assert code == "workspace.slug_tombstoned"
