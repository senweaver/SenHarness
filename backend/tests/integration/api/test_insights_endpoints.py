"""End-to-end tests for the M4.5 insights REST surface.

Two routes; each gets a happy + RBAC path:

* ``POST /insights/generate`` — happy queue + cross-tenant
  session_id rejection.
* ``GET /insights/recent`` — happy list scoped to the calling
  identity + cross-identity isolation.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"insights-api-{uuid.uuid4().hex[:8]}@example.com"
    password = "insights-api-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Insights API", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Insights API WS", "slug": f"iapi-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id

    r = await async_client.post("/api/v1/sessions", headers=headers, json={"kind": "p2p"})
    sid = r.json()["id"]
    return headers, ws_id, sid


async def test_generate_happy_path(async_client):
    headers, _ws_id, sid = await _bootstrap(async_client)

    async def fake_enqueue(**kwargs):
        _ = kwargs
        return "fake-job-1"

    with patch("app.services.cross_session_insights._enqueue_generate", fake_enqueue):
        r = await async_client.post(
            "/api/v1/insights/generate",
            headers=headers,
            json={"return_session_id": sid, "days": 14},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] is True
    assert body["days"] == 14
    assert body["expected_completion_seconds"] == 30
    assert body["job_id"] == "fake-job-1"


async def test_generate_rejects_other_workspace_session(async_client):
    headers_a, _, sid_a = await _bootstrap(async_client)
    headers_b, _, _sid_b = await _bootstrap(async_client)

    # Caller B tries to summarise into Caller A's session — must 404.
    r = await async_client.post(
        "/api/v1/insights/generate",
        headers=headers_b,
        json={"return_session_id": sid_a, "days": 7},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "session.not_found"


async def test_generate_rejects_days_above_max(async_client):
    headers, _ws_id, sid = await _bootstrap(async_client)
    # 999 is above the schema-level fastapi cap (180); FastAPI rejects
    # before our service ever sees the call.
    r = await async_client.post(
        "/api/v1/insights/generate",
        headers=headers,
        json={"return_session_id": sid, "days": 999},
    )
    assert r.status_code == 422


async def test_recent_happy_path_scopes_to_caller(async_client):
    headers, ws_id, sid = await _bootstrap(async_client)

    # Seed two ``insights.cross_session_summarized`` audits — one
    # belonging to the caller, one to a foreign identity. The recent
    # endpoint must surface only the caller's row.
    from app.core.security import decode_token
    from app.db.session import get_session_factory
    from app.services import audit as audit_svc

    raw = headers["Authorization"].split(" ", 1)[1]
    identity_id = uuid.UUID(decode_token(raw, expected_kind="access")["sub"])
    foreign_identity = uuid.uuid4()

    factory = get_session_factory()
    async with factory() as db:
        await audit_svc.record(
            db,
            action="insights.cross_session_summarized",
            actor_identity_id=identity_id,
            workspace_id=uuid.UUID(ws_id),
            resource_type="session",
            resource_id=uuid.UUID(sid),
            summary="caller insight",
            metadata={
                "days": 30,
                "artifact_count": 12,
                "item_count": 4,
                "aux_model": "test:fake",
                "degraded": False,
            },
        )
        await audit_svc.record(
            db,
            action="insights.cross_session_summarized",
            actor_identity_id=foreign_identity,
            workspace_id=uuid.UUID(ws_id),
            resource_type="session",
            resource_id=uuid.UUID(sid),
            summary="foreign insight",
            metadata={
                "days": 7,
                "artifact_count": 3,
                "item_count": 1,
            },
        )
        await db.commit()

    r = await async_client.get("/api/v1/insights/recent?days=30&limit=20", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["days"] == 30
    assert items[0]["artifact_count"] == 12
    assert items[0]["item_count"] == 4
    assert items[0]["aux_model"] == "test:fake"
    assert items[0]["degraded"] is False
    assert items[0]["session_id"] == sid


async def test_recent_requires_workspace_membership(async_client):
    headers, _ws_id, _sid = await _bootstrap(async_client)
    # Strip the workspace header — should land on the
    # ``auth.no_active_workspace`` 401.
    headers_no_ws = {k: v for k, v in headers.items() if k != "X-Workspace-Id"}
    r = await async_client.get("/api/v1/insights/recent", headers=headers_no_ws)
    assert r.status_code == 401
    assert r.json()["code"] == "auth.no_active_workspace"
