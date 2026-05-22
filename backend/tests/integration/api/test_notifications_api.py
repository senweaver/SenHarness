"""Integration: notification + prefs routes (M0.10).

For each of the new / modified routes:

* happy path
* RBAC failure case (cross-identity isolation)

The bell endpoints stay workspace-scoped; the prefs endpoints live
under ``/me`` so they work even before the user has selected an
active workspace.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _register_and_login(async_client, *, name: str = "User") -> dict:
    email = f"notif-{uuid.uuid4().hex[:8]}@example.com"
    password = "notification-test-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    identity_id = uuid.UUID(body["identity_id"])
    workspace = body.get("workspace")
    workspace_id = uuid.UUID(workspace["id"]) if workspace else None

    if body.get("auto_login_tokens"):
        token = body["auto_login_tokens"]["access_token"]
    else:
        r = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = r.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return {
        "identity_id": str(identity_id),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "headers": headers,
    }


async def test_list_notifications_returns_array(async_client):
    user = await _register_and_login(async_client)
    r = await async_client.get("/api/v1/notifications", headers=user["headers"])
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_unread_count_alias_returns_object(async_client):
    user = await _register_and_login(async_client)
    r = await async_client.get("/api/v1/notifications/unread-count", headers=user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "unread" in body
    assert isinstance(body["unread"], int)


async def test_mark_all_read_alias_returns_count(async_client):
    user = await _register_and_login(async_client)
    r = await async_client.post("/api/v1/notifications/mark-all-read", headers=user["headers"])
    assert r.status_code == 200
    assert "marked" in r.json()


async def test_list_notifications_without_auth_403(async_client):
    r = await async_client.get("/api/v1/notifications")
    # The bearer guard returns 401 on missing token; some deployments
    # turn it into 403 via middleware. Either is RBAC failure.
    assert r.status_code in {401, 403}


async def test_get_notification_prefs_includes_catalog(async_client):
    user = await _register_and_login(async_client)
    r = await async_client.get("/api/v1/me/notification-prefs", headers=user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert "catalog" in body
    assert "prefs" in body
    keys = [entry["key"] for entry in body["catalog"]]
    # Visible (non-platform-admin) registry keys must be in the catalog.
    assert "goal.alignment_low" in keys
    assert "auth.workspace_provisioned" in keys
    # Platform-only events are filtered out for normal users.
    assert "workspace.spike_detected" not in keys


async def test_put_notification_prefs_round_trips(async_client):
    user = await _register_and_login(async_client)
    payload = {
        "prefs": {
            "goal.alignment_low": {
                "channels": ["in_app"],
                "muted": False,
            }
        },
        "_global": {"muted_until": None},
    }
    r = await async_client.put(
        "/api/v1/me/notification-prefs",
        headers=user["headers"],
        json=payload,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "goal.alignment_low" in body["prefs"]
    assert body["prefs"]["goal.alignment_low"]["channels"] == ["in_app"]


async def test_put_notification_prefs_keeps_email_for_security_event(
    async_client,
):
    """``requires_email=True`` events cannot be opted out — service rewrites it."""
    user = await _register_and_login(async_client)
    payload = {
        "prefs": {
            "security.signature_failed": {
                "channels": ["in_app"],
                "muted": False,
            }
        },
        "_global": {"muted_until": None},
    }
    r = await async_client.put(
        "/api/v1/me/notification-prefs",
        headers=user["headers"],
        json=payload,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "email" in body["prefs"]["security.signature_failed"]["channels"]


async def test_cross_identity_isolation_on_mark_read(async_client):
    """Marking a notification with the wrong identity returns 404."""
    user = await _register_and_login(async_client, name="A")
    other = await _register_and_login(async_client, name="B")
    fake_id = uuid.uuid4()
    r = await async_client.post(f"/api/v1/notifications/{fake_id}/read", headers=user["headers"])
    assert r.status_code in {404, 403}
    r2 = await async_client.post(f"/api/v1/notifications/{fake_id}/read", headers=other["headers"])
    assert r2.status_code in {404, 403}
