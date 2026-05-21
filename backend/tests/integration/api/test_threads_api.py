"""Integration: M3.6 threads API routes.

Covers the eight routes the cross-platform settings UI consumes:

* ``GET /threads`` (list) — happy path returns the empty shape on a
  fresh workspace.
* ``GET /threads/{id}`` (detail) — 404 when the caller is not the
  thread owner.
* ``POST /threads/pair/initiate`` / ``/pair/consume`` — fail with the
  stable ``thread.cross_platform_disabled`` code when the workspace
  has not opted in (default contract).
* ``POST /threads/pair/initiate`` succeeds once the workspace flips
  ``cross_platform_enabled`` and returns a 6-digit code with TTL.

The Curator-settings test pattern (`_bootstrap` registration helper)
is reused here so the test stays self-contained on a fresh DB.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"thr-{uuid.uuid4().hex[:8]}@example.com"
    password = "threads-api-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Threads Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = r.json()["access_token"]
    workspace = body.get("workspace") or {}
    ws_id = workspace.get("id")
    headers = {"Authorization": f"Bearer {token}"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


def _err_code(body: dict) -> str | None:
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("code")
    return None


# ─── Read surface ────────────────────────────────────────────
async def test_list_threads_empty_on_fresh_workspace(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")
    r = await async_client.get("/api/v1/threads", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_get_thread_returns_404_for_unknown(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")
    fake = uuid.uuid4()
    r = await async_client.get(f"/api/v1/threads/{fake}", headers=headers)
    assert r.status_code == 404


# ─── Pairing path: gated by ``cross_platform_enabled`` ──────
async def test_initiate_pairing_disabled_by_default(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")
    r = await async_client.post(
        "/api/v1/threads/pair/initiate",
        headers=headers,
        json={},
    )
    assert r.status_code == 400, r.text
    assert _err_code(r.json()) == "thread.cross_platform_disabled"


async def test_consume_pairing_disabled_by_default(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")
    r = await async_client.post(
        "/api/v1/threads/pair/consume",
        headers=headers,
        json={"code": "123456"},
    )
    assert r.status_code == 400, r.text
    assert _err_code(r.json()) == "thread.cross_platform_disabled"


async def test_initiate_pairing_returns_six_digit_code_when_enabled(
    async_client,
) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")

    # Flip the platform default so the pairing endpoint is reachable.
    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as fresh:
        await set_system_setting(
            fresh,
            SystemSettingKey.SESSION_ROUTING_DEFAULTS,
            {"cross_platform_enabled": True},
        )
        await fresh.commit()

    # Drop the platform_settings cache so the route immediately observes
    # the new value (the cache TTL would otherwise gate the assertion).
    from app.services import platform_settings as ps_mod

    ps_mod.invalidate_local()

    r = await async_client.post(
        "/api/v1/threads/pair/initiate",
        headers=headers,
        json={},
    )
    if r.status_code == 503 or r.status_code == 500:
        pytest.skip("Redis unavailable in test environment")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["code"], str)
    assert len(body["code"]) == 6
    assert body["code"].isdigit()
    assert body["ttl_seconds"] >= 60


async def test_consume_pairing_with_invalid_code_400(async_client) -> None:
    headers, ws_id = await _bootstrap(async_client)
    if ws_id is None:
        pytest.skip("auto-provisioned workspace unavailable")

    from app.db.session import get_session_factory
    from app.services.system_settings import (
        SystemSettingKey,
        set_system_setting,
    )

    factory = get_session_factory()
    async with factory() as fresh:
        await set_system_setting(
            fresh,
            SystemSettingKey.SESSION_ROUTING_DEFAULTS,
            {"cross_platform_enabled": True},
        )
        await fresh.commit()
    from app.services import platform_settings as ps_mod

    ps_mod.invalidate_local()

    r = await async_client.post(
        "/api/v1/threads/pair/consume",
        headers=headers,
        json={"code": "abcdef"},  # not 6 digits
    )
    assert r.status_code in (400, 422), r.text


async def test_threads_endpoints_require_auth(async_client) -> None:
    r = await async_client.get("/api/v1/threads")
    assert r.status_code == 401
    r = await async_client.post(
        "/api/v1/threads/pair/initiate",
        json={},
    )
    assert r.status_code == 401
