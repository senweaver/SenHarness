"""End-to-end tests for the M0.2 session artifact REST surface.

Covers each route with a happy path plus at least one RBAC failure,
and the cross-workspace isolation case demanded by the cross-cutting
checklist.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"artifacts-{uuid.uuid4().hex[:8]}@example.com"
    password = "artifacts-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Artifacts Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Artifacts WS", "slug": f"artifacts-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _new_session(async_client, headers) -> str:
    r = await async_client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"kind": "p2p"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _seed_artifact(
    *,
    workspace_id: str,
    session_id: str,
    identity_id: str,
    final_outcome: str = "success",
    events: list[dict] | None = None,
) -> str:
    """Insert an artifact directly through the service so we don't have
    to spin up a full agent run inside the test process."""
    from app.db.session import get_session_factory
    from app.services import session_artifact as artifact_svc

    factory: async_sessionmaker = get_session_factory()
    async with factory() as db:
        row = await artifact_svc.capture_artifact(
            db,
            run_id=uuid.uuid4(),
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            user_text="seed",
            events=events or [{"kind": "delta", "data": {"text": "ok"}}],
            final_outcome=final_outcome,
        )
        await db.commit()
        return str(row.id)


def _identity_id_from_token(headers: dict) -> str:
    """Extract the JWT subject so seeded artifacts attribute to the caller."""
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


# ─── Happy paths ─────────────────────────────────────────────
async def test_list_session_artifacts_returns_seeded_row(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    r = await async_client.get(
        f"/api/v1/sessions/{sid}/artifacts", headers=headers
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == aid
    assert rows[0]["final_outcome"] == "success"
    # Lineage column is exposed even when empty.
    assert "turns_json" in rows[0]
    assert rows[0]["turns_json"][0]["role"] == "user"


async def test_get_single_artifact_happy_path(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    r = await async_client.get(f"/api/v1/artifacts/{aid}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == aid
    assert body["session_id"] == sid


async def test_workspace_recent_admin_path(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    r = await async_client.get(
        f"/api/v1/workspaces/{ws_id}/artifacts/recent?since_hours=24",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1


# ─── RBAC failures ───────────────────────────────────────────
async def test_get_other_workspace_artifact_404(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    aid = await _seed_artifact(
        workspace_id=ws_a,
        session_id=sid_a,
        identity_id=_identity_id_from_token(headers_a),
    )

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(f"/api/v1/artifacts/{aid}", headers=headers_b)
    assert r.status_code in (403, 404), r.text


async def test_list_session_artifacts_other_workspace_404(async_client):
    headers_a, _ = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/sessions/{sid_a}/artifacts", headers=headers_b
    )
    assert r.status_code in (403, 404)


async def test_workspace_recent_requires_admin_match(async_client):
    _headers_a, ws_a = await _bootstrap(async_client)
    headers_b, _ = await _bootstrap(async_client)

    # Workspace B's bearer asking for workspace A's recent feed must 404.
    r = await async_client.get(
        f"/api/v1/workspaces/{ws_a}/artifacts/recent",
        headers=headers_b,
    )
    assert r.status_code in (403, 404)


async def test_unauthenticated_artifact_read_blocked(async_client):
    aid = uuid.uuid4()
    r = await async_client.get(f"/api/v1/artifacts/{aid}")
    assert r.status_code in (401, 403)
