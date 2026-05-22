"""Integration: M1.9 curator settings routes.

Hits ``GET /workspaces/{id}/settings/curator``,
``PATCH /workspaces/{id}/settings/curator``,
``POST /workspaces/{id}/settings/curator/run-now``, and
``GET /workspaces/{id}/settings/curator/last-run``.

The curator service module (``app.services.skill_curator``) is the
parallel M1.4 deliverable. When it has not yet shipped, ``run-now``
must respond with a structured 503 ``curator.service_not_ready`` and
``last-run`` must still serve historical audit rows; both behaviours
have explicit cases below. When the module is present we
monkey-patch ``trigger_curator_now`` to return a deterministic
fixture so we keep the test free of background side effects.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.audit import AuditEvent
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"cura-{uuid.uuid4().hex[:8]}@example.com"
    password = "curator-config-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Curator Tester", "password": password},
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


async def _add_member(async_client, headers, ws_id) -> dict | None:
    """Invite a fresh user as MEMBER (non-admin). Returns their headers
    dict or None when the invitation pipeline is not available in the
    test environment."""
    inv = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        headers=headers,
        json={"role": "member"},
    )
    if inv.status_code != 201:
        return None
    code = inv.json()["code"]

    headers_member, _ = await _bootstrap(async_client)
    accept = await async_client.post(
        "/api/v1/workspaces/invitations/accept",
        headers=headers_member,
        json={"code": code},
    )
    if accept.status_code not in (200, 201):
        return None
    headers_member["X-Workspace-Id"] = ws_id
    return headers_member


def _err_code(body: dict) -> str | None:
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("code")
    return body.get("code")


# ─── GET ─────────────────────────────────────────────────────
async def test_get_returns_platform_default_when_no_override(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.get(f"/api/v1/workspaces/{ws_id}/settings/curator", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["stale_after_days"] == 30
    assert body["archive_after_days"] == 90
    assert body["min_idle_hours"] == 24
    assert body["active_skills_soft_cap"] == 50
    assert all(v == "platform_default" for v in body["source"].values())


# ─── PATCH ───────────────────────────────────────────────────
async def test_patch_sets_workspace_override(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.patch(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=headers,
        json={"stale_after_days": 14},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_after_days"] == 14
    assert body["source"]["stale_after_days"] == "workspace"
    assert body["source"]["archive_after_days"] == "platform_default"

    r2 = await async_client.get(f"/api/v1/workspaces/{ws_id}/settings/curator", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["stale_after_days"] == 14


async def test_patch_writes_audit_diff(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.patch(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=headers,
        json={"stale_after_days": 7, "min_idle_hours": 1},
    )
    assert r.status_code == 200, r.text

    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import desc, select

        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                        AuditEvent.action == "workspace.curator_settings_updated",
                    )
                    .order_by(desc(AuditEvent.created_at))
                )
            )
            .scalars()
            .all()
        )
    assert rows, "expected an audit row for the curator settings update"
    diff = (rows[0].metadata_json or {}).get("diff", {})
    assert diff.get("stale_after_days", {}).get("from") == 30
    assert diff.get("stale_after_days", {}).get("to") == 7
    assert "min_idle_hours" in diff


async def test_patch_non_admin_returns_403(async_client):
    headers, ws_id = await _bootstrap(async_client)
    member_headers = await _add_member(async_client, headers, ws_id)
    if member_headers is None:
        pytest.skip("invitation pipeline unavailable in this environment")

    r = await async_client.patch(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=member_headers,
        json={"stale_after_days": 7},
    )
    assert r.status_code == 403, r.text


async def test_patch_member_can_get(async_client):
    headers, ws_id = await _bootstrap(async_client)
    member_headers = await _add_member(async_client, headers, ws_id)
    if member_headers is None:
        pytest.skip("invitation pipeline unavailable in this environment")
    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=member_headers,
    )
    assert r.status_code == 200, r.text


async def test_patch_stale_gt_archive_returns_422(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.patch(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=headers,
        json={"stale_after_days": 100, "archive_after_days": 50},
    )
    assert r.status_code == 422, r.text


async def test_patch_negative_value_returns_422(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.patch(
        f"/api/v1/workspaces/{ws_id}/settings/curator",
        headers=headers,
        json={"stale_after_days": -1},
    )
    assert r.status_code == 422


# ─── Cross-workspace isolation ────────────────────────────────
async def test_cross_workspace_get_returns_403(async_client):
    _headers_a, ws_a = await _bootstrap(async_client)
    headers_b, _ws_b = await _bootstrap(async_client)
    # B reads A's curator config — must 403 (not a member).
    r = await async_client.get(
        f"/api/v1/workspaces/{ws_a}/settings/curator",
        headers={"Authorization": headers_b["Authorization"]},
    )
    assert r.status_code == 403, r.text


# ─── POST run-now ─────────────────────────────────────────────
async def test_run_now_returns_503_when_service_missing(async_client, monkeypatch):
    """When the M1.4 service module is absent the route signals a
    structured 503 instead of crashing.

    Sets ``sys.modules["app.services.skill_curator"] = None`` so the
    ``from app.services import skill_curator`` statement raises
    ``ModuleNotFoundError`` (a subclass of ``ImportError``); also
    detaches any cached attribute on the ``app.services`` package so
    a previously-imported module doesn't bypass the sys.modules
    consultation.
    """
    import sys

    import app.services as svcs

    headers, ws_id = await _bootstrap(async_client)
    monkeypatch.delattr(svcs, "skill_curator", raising=False)
    monkeypatch.setitem(sys.modules, "app.services.skill_curator", None)

    r = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/settings/curator/run-now",
        headers=headers,
    )
    assert r.status_code == 503, r.text
    assert _err_code(r.json()) == "curator.service_not_ready"


async def test_run_now_invokes_service_when_present(async_client, monkeypatch):
    """When the service is wired the endpoint forwards the call and
    audits ``curator.run_now_triggered``."""
    import sys
    import types

    import app.services as svcs

    headers, ws_id = await _bootstrap(async_client)

    started = datetime.now(UTC).replace(microsecond=0)
    finished = started + timedelta(seconds=2)

    async def _trigger(db, *, workspace_id):
        return {
            "workspace_id": str(workspace_id),
            "stale_proposed": 3,
            "archive_proposed": 1,
            "pinned_skipped": 2,
            "duration_ms": 2000,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        }

    fake = types.ModuleType("app.services.skill_curator")
    fake.trigger_curator_now = _trigger
    monkeypatch.setitem(sys.modules, "app.services.skill_curator", fake)
    monkeypatch.setattr(svcs, "skill_curator", fake, raising=False)

    r = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/settings/curator/run-now",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_proposed"] == 3
    assert body["archive_proposed"] == 1
    assert body["pinned_skipped"] == 2
    assert body["duration_ms"] == 2000

    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import desc, select

        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                        AuditEvent.action == "curator.run_now_triggered",
                    )
                    .order_by(desc(AuditEvent.created_at))
                )
            )
            .scalars()
            .all()
        )
    assert rows, "expected curator.run_now_triggered audit row"


async def test_run_now_non_admin_returns_403(async_client, monkeypatch):
    import sys
    import types

    import app.services as svcs

    headers, ws_id = await _bootstrap(async_client)
    member_headers = await _add_member(async_client, headers, ws_id)
    if member_headers is None:
        pytest.skip("invitation pipeline unavailable in this environment")

    async def _trigger(db, *, workspace_id):
        return {
            "workspace_id": str(workspace_id),
            "stale_proposed": 0,
            "archive_proposed": 0,
            "pinned_skipped": 0,
            "duration_ms": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }

    fake = types.ModuleType("app.services.skill_curator")
    fake.trigger_curator_now = _trigger
    monkeypatch.setitem(sys.modules, "app.services.skill_curator", fake)
    monkeypatch.setattr(svcs, "skill_curator", fake, raising=False)

    r = await async_client.post(
        f"/api/v1/workspaces/{ws_id}/settings/curator/run-now",
        headers=member_headers,
    )
    assert r.status_code == 403, r.text


async def test_run_now_rate_limit_trips_at_third_call(async_client, monkeypatch):
    """Bucket is 2 / 5min; the third consecutive call from the same
    identity should return 429."""
    import sys
    import types

    import app.services as svcs

    headers, ws_id = await _bootstrap(async_client)

    async def _trigger(db, *, workspace_id):
        return {
            "workspace_id": str(workspace_id),
            "stale_proposed": 0,
            "archive_proposed": 0,
            "pinned_skipped": 0,
            "duration_ms": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }

    fake = types.ModuleType("app.services.skill_curator")
    fake.trigger_curator_now = _trigger
    monkeypatch.setitem(sys.modules, "app.services.skill_curator", fake)
    monkeypatch.setattr(svcs, "skill_curator", fake, raising=False)

    statuses = []
    for _ in range(3):
        r = await async_client.post(
            f"/api/v1/workspaces/{ws_id}/settings/curator/run-now",
            headers=headers,
        )
        statuses.append(r.status_code)

    assert 429 in statuses, f"expected a 429 within 3 consecutive calls, got {statuses}"


# ─── GET last-run ─────────────────────────────────────────────
async def test_last_run_empty_when_never_swept(async_client):
    headers, ws_id = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/settings/curator/last-run",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_run_at"] is None
    assert body["last_result"] is None


async def test_last_run_reads_curator_swept_audit(async_client):
    """Manually insert a ``curator.swept`` audit row and verify the
    endpoint reflects it."""
    from app.repositories.audit import AuditRepository

    headers, ws_id = await _bootstrap(async_client)

    started = datetime.now(UTC).replace(microsecond=0)
    finished = started + timedelta(seconds=5)
    factory = get_session_factory()
    async with factory() as db:
        await AuditRepository(db).add(
            workspace_id=uuid.UUID(ws_id),
            actor_identity_id=None,
            action="curator.swept",
            resource_type="workspace",
            resource_id=uuid.UUID(ws_id),
            summary="curator swept",
            metadata_json={
                "stale_proposed": 7,
                "archive_proposed": 2,
                "pinned_skipped": 1,
                "duration_ms": 5000,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
            },
        )
        await db.commit()

    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/settings/curator/last-run",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_run_at"] is not None
    result = body["last_result"]
    assert result is not None
    assert result["stale_proposed"] == 7
    assert result["archive_proposed"] == 2
    assert result["pinned_skipped"] == 1
    assert result["duration_ms"] == 5000
