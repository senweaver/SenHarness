"""HTTP-level smoke tests for the M0.13 admin endpoints."""

from __future__ import annotations

import pytest

from app.db.models.identity import PlatformRole
from app.repositories.identity import IdentityRepository

pytestmark = pytest.mark.asyncio


async def _make_admin_token(db_session, identity):
    """Promote ``identity`` to platform_admin and mint an access token."""
    from app.core.security import create_access_token

    repo = IdentityRepository(db_session)
    promoted = await repo.update(
        identity, platform_role=PlatformRole.PLATFORM_ADMIN
    )
    await db_session.commit()
    token, _, _ = create_access_token(
        identity_id=str(promoted.id),
        workspace_id=None,
        roles=["platform_admin"],
    )
    return f"Bearer {token}"


async def test_list_endpoint_returns_14_sections(
    async_client, db_session, identity
):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.get(
        "/api/v1/admin/platform-settings", headers={"Authorization": auth}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["sections"]) == 14
    keys = {s["section"] for s in body["sections"]}
    assert "general" in keys
    assert "email.smtp" in keys


async def test_get_section_endpoint(async_client, db_session, identity):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.get(
        "/api/v1/admin/platform-settings/general",
        headers={"Authorization": auth},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["section"] == "general"
    assert "site_name" in body["value"]


async def test_get_schema_endpoint(async_client, db_session, identity):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.get(
        "/api/v1/admin/platform-settings/general/schema",
        headers={"Authorization": auth},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "properties" in body


async def test_put_section_happy_path(async_client, db_session, identity):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.put(
        "/api/v1/admin/platform-settings/general",
        headers={"Authorization": auth},
        json={
            "value": {
                "site_name": "MyCorp",
                "primary_color_hex": "#102030",
                "default_locale": "en-US",
                "default_timezone": "UTC",
            },
            "confirmed_dangerous": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["value"]["site_name"] == "MyCorp"
    assert body["db_present"] is True


async def test_put_section_dangerous_rejected_without_confirmation(
    async_client, db_session, identity
):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.put(
        "/api/v1/admin/platform-settings/security.sandbox",
        headers={"Authorization": auth},
        json={
            "value": {
                "allow_local_execute_in_prod": True,
                "allow_ssh_backend": False,
                "require_command_allowlist_in_prod": True,
            },
            "confirmed_dangerous": False,
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["code"].startswith("platform_settings.")


async def test_put_section_dangerous_passes_with_confirmation(
    async_client, db_session, identity
):
    auth = await _make_admin_token(db_session, identity)
    res = await async_client.put(
        "/api/v1/admin/platform-settings/security.sandbox",
        headers={"Authorization": auth},
        json={
            "value": {
                "allow_local_execute_in_prod": True,
                "allow_ssh_backend": False,
                "require_command_allowlist_in_prod": True,
            },
            "confirmed_dangerous": True,
        },
    )
    assert res.status_code == 200, res.text


async def test_reset_endpoint_restores_defaults(
    async_client, db_session, identity
):
    auth = await _make_admin_token(db_session, identity)
    await async_client.put(
        "/api/v1/admin/platform-settings/general",
        headers={"Authorization": auth},
        json={
            "value": {
                "site_name": "Mutated",
                "primary_color_hex": "#000000",
                "default_locale": "en-US",
                "default_timezone": "UTC",
            },
            "confirmed_dangerous": False,
        },
    )
    res = await async_client.post(
        "/api/v1/admin/platform-settings/general/reset",
        headers={"Authorization": auth},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["value"]["site_name"] == "SenHarness"


async def test_non_admin_gets_403(async_client, identity, db_session):
    """Plain user (no platform_admin role) hits the gate."""
    from app.core.security import create_access_token

    token, _, _ = create_access_token(
        identity_id=str(identity.id), workspace_id=None, roles=["user"]
    )
    res = await async_client.get(
        "/api/v1/admin/platform-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
