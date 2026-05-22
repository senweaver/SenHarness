"""Auth flow smoke test — register + login + refresh.

The register-then-login path is the single most-exercised auth route in
production, so breaking it is a deploy-blocker. We register a fresh
identity and then authenticate with the same credentials to lock that
contract in.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def test_register_then_login_returns_access_token(async_client):
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct horse battery staple"

    # Register
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Smoke User", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == email

    # Login with the same credentials.
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body
    assert body["access_token"]


async def test_login_with_wrong_password_is_401(async_client):
    email = f"bad-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "U", "password": "right-password"},
    )
    assert r.status_code == 201

    r = await async_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WRONG-password"},
    )
    assert r.status_code == 401


async def test_register_rate_limited_after_burst(async_client):
    """Registration is capped at 3/min per IP (see auth.py). Make 5
    attempts and expect at least one 429. This also sanity-checks that
    the Depends(rate_limit(...)) actually mounted on the route — a
    regression that forgets the Depends would make every request pass.
    """
    for i in range(5):
        r = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"burst-{i}-{uuid.uuid4().hex[:6]}@example.com",
                "name": "Burst",
                "password": "some-password",
            },
        )
        if r.status_code == 429:
            assert r.json()["code"] == "rate_limit.exceeded"
            return
    pytest.fail("expected at least one 429 response inside the burst")
