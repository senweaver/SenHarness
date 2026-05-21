"""Integration tests for the M3.5/M3.9 ``/admin/plugins`` surface.

Six endpoints, each exercised twice: one happy-path call as a
platform admin, one RBAC failure call as a regular member. The
goal is to lock the gate (only ``platform_admin`` can act) and the
audit fan-out shape; the per-endpoint business logic is covered by
the loader and signing unit tests.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models.audit import AuditEvent
from app.db.models.identity import PlatformRole
from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus

pytestmark = pytest.mark.asyncio


def _bearer(identity_id: uuid.UUID) -> dict[str, str]:
    token, _, _ = create_access_token(
        identity_id=str(identity_id), workspace_id=None, roles=[]
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_admin(db_session, identity) -> None:
    identity.platform_role = PlatformRole.PLATFORM_ADMIN
    await db_session.flush()
    await db_session.commit()


async def _seed_registry(db_session, *, name: str = "alpha") -> PluginRegistry:
    row = PluginRegistry(
        name=name,
        version="0.0.1",
        sha256="a" * 64,
        capability_scopes=["pre_tool_call"],
        status=PluginRegistryStatus.DISCOVERED,
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()
    return row


# ── GET /admin/plugins ──────────────────────────────────────
async def test_list_plugins_happy_path(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    await _seed_registry(db_session)
    resp = await async_client.get(
        "/api/v1/admin/plugins", headers=_bearer(identity.id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert any(row["name"] == "alpha" for row in body)


async def test_list_plugins_rejects_non_admin(async_client, db_session, identity):
    resp = await async_client.get(
        "/api/v1/admin/plugins", headers=_bearer(identity.id)
    )
    assert resp.status_code == 403


# ── GET /admin/plugins/{id} ─────────────────────────────────
async def test_get_plugin_happy(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    row = await _seed_registry(db_session)
    resp = await async_client.get(
        f"/api/v1/admin/plugins/{row.id}", headers=_bearer(identity.id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(row.id)
    assert body["name"] == "alpha"


async def test_get_plugin_rejects_non_admin(async_client, db_session, identity):
    row = await _seed_registry(db_session)
    resp = await async_client.get(
        f"/api/v1/admin/plugins/{row.id}", headers=_bearer(identity.id)
    )
    assert resp.status_code == 403


async def test_get_plugin_404_for_unknown_id(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    resp = await async_client.get(
        f"/api/v1/admin/plugins/{uuid.uuid4()}",
        headers=_bearer(identity.id),
    )
    assert resp.status_code == 404


# ── POST /admin/plugins/{id}/approve ────────────────────────
async def test_approve_plugin_happy(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    row = await _seed_registry(db_session)
    # ``reload=False`` because the approve endpoint will otherwise
    # re-run load_and_register_plugins and the test fixture has no
    # plugin folder to scan; the gate-only test focuses on the
    # state transition + audit row.
    resp = await async_client.post(
        f"/api/v1/admin/plugins/{row.id}/approve",
        headers=_bearer(identity.id),
        json={"reload": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approved_by_platform_admin"] is True
    assert body["status"] == PluginRegistryStatus.APPROVED.value

    audits = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "plugin.approved_by_admin"
            )
        )
    ).scalars().all()
    assert any(a.resource_id == row.id for a in audits)


async def test_approve_plugin_rejects_non_admin(
    async_client, db_session, identity
):
    row = await _seed_registry(db_session)
    resp = await async_client.post(
        f"/api/v1/admin/plugins/{row.id}/approve",
        headers=_bearer(identity.id),
        json={"reload": False},
    )
    assert resp.status_code == 403


# ── POST /admin/plugins/{id}/reject ─────────────────────────
async def test_reject_plugin_happy(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    row = await _seed_registry(db_session)
    resp = await async_client.post(
        f"/api/v1/admin/plugins/{row.id}/reject",
        headers=_bearer(identity.id),
        json={"reason": "fails review"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == PluginRegistryStatus.REJECTED.value
    assert body["approved_by_platform_admin"] is False


async def test_reject_plugin_rejects_non_admin(
    async_client, db_session, identity
):
    row = await _seed_registry(db_session)
    resp = await async_client.post(
        f"/api/v1/admin/plugins/{row.id}/reject",
        headers=_bearer(identity.id),
        json={"reason": "x"},
    )
    assert resp.status_code == 403


# ── POST /admin/plugins/scan ────────────────────────────────
async def test_scan_plugins_happy(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    resp = await async_client.post(
        "/api/v1/admin/plugins/scan", headers=_bearer(identity.id)
    )
    # Without an on-disk plugins directory the scan returns 0
    # discovered plus the seeded rows total. We just verify the
    # contract (200 + ScanResult shape).
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "discovered" in body
    assert "rows_total" in body
    assert "new_rows" in body


async def test_scan_plugins_rejects_non_admin(async_client, db_session, identity):
    resp = await async_client.post(
        "/api/v1/admin/plugins/scan", headers=_bearer(identity.id)
    )
    assert resp.status_code == 403


# ── POST /admin/plugins/reload ──────────────────────────────
async def test_reload_plugins_happy(async_client, db_session, identity):
    await _make_admin(db_session, identity)
    resp = await async_client.post(
        "/api/v1/admin/plugins/reload", headers=_bearer(identity.id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "loaded" in body
    assert "plugin_dir" in body
    assert "allow_user_plugins" in body


async def test_reload_plugins_rejects_non_admin(
    async_client, db_session, identity
):
    resp = await async_client.post(
        "/api/v1/admin/plugins/reload", headers=_bearer(identity.id)
    )
    assert resp.status_code == 403
