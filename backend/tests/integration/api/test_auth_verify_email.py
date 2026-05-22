"""End-to-end tests for /auth/verify-email and the PENDING gate (M0.9)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _enable_verification():
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, True)
        await db.commit()


async def _reset_verification():
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, False)
        await db.commit()


async def _register_pending(async_client) -> tuple[str, str]:
    """Returns (email, plaintext_token)."""
    from sqlalchemy import select

    from app.db.models.email_verification import EmailVerificationToken
    from app.db.session import get_session_factory

    email = f"pending-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Pending Pat",
            "password": "correct horse battery staple",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"

    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.identity import IdentityRepository

        ident = await IdentityRepository(db).get_by_email(email)
        assert ident is not None
        rows = (
            (
                await db.execute(
                    select(EmailVerificationToken)
                    .where(EmailVerificationToken.identity_id == ident.id)
                    .order_by(EmailVerificationToken.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert rows
        # The plaintext is gone — only the hash is stored. We need to mint
        # a fresh known-plaintext token via the service so the integration
        # test can hit the endpoint with a known value.
        from app.services import email_verification as svc

        token = await svc.issue_token(db, identity_id=ident.id)
        await db.commit()
    return email, token


async def test_verify_email_happy_then_login(async_client):
    await _enable_verification()
    try:
        email, token = await _register_pending(async_client)
        r = await async_client.post(f"/api/v1/auth/verify-email/{token}")
        assert r.status_code == 204, r.text

        r = await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "correct horse battery staple"},
        )
        assert r.status_code == 200, r.text
    finally:
        await _reset_verification()


async def test_verify_email_invalid_token_401(async_client):
    await _enable_verification()
    try:
        r = await async_client.post("/api/v1/auth/verify-email/" + uuid.uuid4().hex)
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "auth.verify_token_invalid"
    finally:
        await _reset_verification()


async def test_verify_email_reuse_rejected(async_client):
    await _enable_verification()
    try:
        _email, token = await _register_pending(async_client)
        r = await async_client.post(f"/api/v1/auth/verify-email/{token}")
        assert r.status_code == 204
        r = await async_client.post(f"/api/v1/auth/verify-email/{token}")
        assert r.status_code == 401
        assert r.json()["code"] == "auth.verify_token_consumed"
    finally:
        await _reset_verification()


async def test_pending_user_cannot_login(async_client):
    await _enable_verification()
    try:
        email, _token = await _register_pending(async_client)
        r = await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "correct horse battery staple"},
        )
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "auth.email_unverified"
    finally:
        await _reset_verification()
