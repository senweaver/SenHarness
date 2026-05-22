"""Integration: M3.7 ``/me/profile`` endpoints.

Exercises the four routes:

* ``GET /me/profile`` — reads the per-dimension bundle.
* ``POST /me/profile/{fact_id}/confirm`` — toggles ``user_confirmed``.
* ``POST /me/profile/{fact_id}/reject`` — toggles ``user_rejected``.
* ``POST /me/profile/extract-now`` — manual aux extract (monkey-patched).

Cross-identity isolation is asserted by reusing one fact_id from a
different identity's session and expecting a 404.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.user_profile import UserProfileDimension
from app.services import user_profile as svc

pytestmark = pytest.mark.asyncio


async def _register_and_login(async_client, *, name: str = "ProfUser") -> dict:
    email = f"prof-{uuid.uuid4().hex[:8]}@example.com"
    password = "user-profile-api-test-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    workspace_id = workspace["id"]
    identity_id = body["identity_id"]
    if body.get("auto_login_tokens"):
        token = body["auto_login_tokens"]["access_token"]
    else:
        r = await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        token = r.json()["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": workspace_id,
    }
    return {
        "workspace_id": workspace_id,
        "identity_id": identity_id,
        "headers": headers,
    }


async def _seed_fact(
    *,
    workspace_id: str,
    identity_id: str,
    dimension: UserProfileDimension,
    confidence: float,
    user_confirmed: bool = False,
    user_rejected: bool = False,
) -> str:
    from app.db.models.user_profile import UserProfileFact
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        row = UserProfileFact(
            workspace_id=uuid.UUID(workspace_id),
            identity_id=uuid.UUID(identity_id),
            dimension=dimension,
            fact=f"fact for {dimension.value}",
            confidence=confidence,
            source_run_ids=[],
            user_confirmed=user_confirmed,
            user_rejected=user_rejected,
        )
        db.add(row)
        await db.commit()
        return str(row.id)


async def test_get_profile_returns_all_12_dimensions(async_client):
    user = await _register_and_login(async_client)
    r = await async_client.get("/api/v1/me/profile", headers=user["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == user["workspace_id"]
    assert body["identity_id"] == user["identity_id"]
    assert len(body["dimensions"]) == len(list(UserProfileDimension))
    assert all(d["active"] is None for d in body["dimensions"])
    assert body["rendered_chars"] == 0


async def test_confirm_toggles_user_confirmed(async_client):
    user = await _register_and_login(async_client)
    fact_id = await _seed_fact(
        workspace_id=user["workspace_id"],
        identity_id=user["identity_id"],
        dimension=UserProfileDimension.TONE_PREFERENCE,
        confidence=0.4,
        user_confirmed=False,
    )
    r = await async_client.post(
        f"/api/v1/me/profile/{fact_id}/confirm",
        headers=user["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_confirmed"] is True
    assert body["user_rejected"] is False


async def test_reject_toggles_user_rejected(async_client):
    user = await _register_and_login(async_client)
    fact_id = await _seed_fact(
        workspace_id=user["workspace_id"],
        identity_id=user["identity_id"],
        dimension=UserProfileDimension.AUTONOMY_TOLERANCE,
        confidence=0.85,
    )
    r = await async_client.post(
        f"/api/v1/me/profile/{fact_id}/reject",
        headers=user["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_rejected"] is True
    assert body["user_confirmed"] is False


async def test_cross_identity_action_404(async_client):
    user_a = await _register_and_login(async_client, name="A")
    user_b = await _register_and_login(async_client, name="B")
    fact_id = await _seed_fact(
        workspace_id=user_a["workspace_id"],
        identity_id=user_a["identity_id"],
        dimension=UserProfileDimension.GOAL_PATTERN,
        confidence=0.85,
    )
    # ``user_b`` cannot confirm or reject ``user_a``'s fact — workspace
    # filter on the service layer plus the repo's identity check both
    # fail to match and surface as a 404.
    r = await async_client.post(
        f"/api/v1/me/profile/{fact_id}/confirm",
        headers=user_b["headers"],
    )
    assert r.status_code == 404


async def test_extract_now_invokes_service(async_client, monkeypatch):
    user = await _register_and_login(async_client)
    called: dict[str, object] = {}

    async def _stub_extract(
        db,
        *,
        workspace_id,
        identity_id,
        since_run_count=10,
        invocation_kind="scheduled",
        actor_identity_id=None,
    ):
        called["workspace_id"] = workspace_id
        called["identity_id"] = identity_id
        called["invocation_kind"] = invocation_kind
        return svc.ExtractOutcome(
            workspace_id=workspace_id,
            identity_id=identity_id,
            facts_created=3,
            facts_unchanged=1,
            artifacts_examined=4,
            duration_ms=42,
        )

    monkeypatch.setattr(svc, "extract_facts_from_runs", _stub_extract)

    r = await async_client.post("/api/v1/me/profile/extract-now", headers=user["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts_created"] == 3
    assert body["facts_unchanged"] == 1
    assert body["artifacts_examined"] == 4
    assert body["duration_ms"] == 42
    assert str(called["workspace_id"]) == user["workspace_id"]
    assert str(called["identity_id"]) == user["identity_id"]
    assert called["invocation_kind"] == "manual"


async def test_unauthenticated_endpoints_reject(async_client):
    r = await async_client.get("/api/v1/me/profile")
    assert r.status_code in {401, 403}
    r = await async_client.post("/api/v1/me/profile/extract-now")
    assert r.status_code in {401, 403}
