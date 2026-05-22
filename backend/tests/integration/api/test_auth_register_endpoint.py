"""End-to-end tests for /auth/register and /auth/registration-mode (M0.9)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _reset_settings(async_client):
    """Best-effort: poke service defaults via direct DB session."""
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.REGISTRATION_MODE, "open_personal")
        await set_system_setting(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, False)
        await db.commit()


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


async def test_register_open_personal_returns_workspace_and_tokens(async_client):
    await _reset_settings(async_client)
    body = {
        "email": _email("reg"),
        "name": "Open Pat",
        "password": "correct horse battery staple",
    }
    r = await async_client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["registration_mode"] == "open_personal"
    assert data["status"] == "active"
    assert data["requires_email_verification"] is False
    assert data["workspace"] is not None
    assert data["workspace"]["slug"]
    assert data["auto_login_tokens"] is not None
    assert data["auto_login_tokens"]["access_token"]


async def test_register_invite_only_without_code_400(async_client):
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.REGISTRATION_MODE, "open_invite_only")
        await db.commit()
    try:
        body = {
            "email": _email("inv"),
            "name": "Iva",
            "password": "correct horse battery staple",
        }
        r = await async_client.post("/api/v1/auth/register", json=body)
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "auth.invitation_required"
    finally:
        await _reset_settings(async_client)


async def test_register_closed_mode_403(async_client):
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.REGISTRATION_MODE, "closed")
        await db.commit()
    try:
        body = {
            "email": _email("cls"),
            "name": "Closed",
            "password": "correct horse battery staple",
        }
        r = await async_client.post("/api/v1/auth/register", json=body)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "auth.registration_closed"
    finally:
        await _reset_settings(async_client)


async def test_register_with_email_verification_returns_pending(async_client):
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, True)
        await db.commit()
    try:
        body = {
            "email": _email("ver"),
            "name": "Verify",
            "password": "correct horse battery staple",
        }
        r = await async_client.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "pending"
        assert data["requires_email_verification"] is True
        assert data["auto_login_tokens"] is None
    finally:
        await _reset_settings(async_client)


async def test_registration_mode_endpoint_public(async_client):
    await _reset_settings(async_client)
    r = await async_client.get("/api/v1/auth/registration-mode")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] in {"open_personal", "open_invite_only", "closed"}
    assert "invitation_required" in body
    assert "requires_email_verification" in body


async def test_slug_warning_only_when_random_path(async_client):
    """Same email local-part registered twice — second should NOT warn (linear -2 path)."""
    await _reset_settings(async_client)
    base = f"slugtest{uuid.uuid4().hex[:6]}"
    r1 = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{base}@example.com",
            "name": "First",
            "password": "correct horse battery staple",
        },
    )
    assert r1.status_code == 201
    assert r1.json()["workspace_slug_warning"] is False

    r2 = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{base}@other.com",
            "name": "Second",
            "password": "correct horse battery staple",
        },
    )
    assert r2.status_code == 201
    assert r2.json()["workspace_slug_warning"] is False
    assert r2.json()["workspace"]["slug"].endswith("-2")
