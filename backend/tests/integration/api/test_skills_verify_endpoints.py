"""Integration: M2.4 verifier endpoints.

* ``POST .../verify-now`` — happy path + non-admin 403 + cross-workspace 404.
* ``GET .../validation`` — returns the persisted blob for a verified version.
* Rate limit gate fires within the 5/300s budget.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.services import skill_verifier as verifier_svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"verify-api-{uuid.uuid4().hex[:8]}@example.com"
    password = "verifier-api-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Verifier API", "password": password},
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


async def _create_pack_with_v2_proposed(async_client, headers) -> tuple[str, str]:
    payload = {
        "slug": f"vfy-{uuid.uuid4().hex[:8]}",
        "name": "Verify pack",
        "version": "0.1.0",
        "manifest_json": {},
        "content_md": "v1 body initial",
    }
    r = await async_client.post("/api/v1/skills/packs", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    pack_id = r.json()["id"]

    r = await async_client.patch(
        f"/api/v1/skills/packs/{pack_id}",
        headers=headers,
        json={"content_md": "v2 body proposed for verification"},
    )
    assert r.status_code == 200, r.text

    # The PATCH path activates v2; the verifier needs a PROPOSED row.
    # We seed one fresh PROPOSED v3 via the version service so the
    # verify-now endpoint has a target in the right state.
    from app.db.session import get_session_factory
    from app.services import skill_version as svc

    factory = get_session_factory()
    async with factory() as db:
        v3 = await svc.create_version(
            db,
            workspace_id=uuid.UUID(headers["X-Workspace-Id"]),
            pack_id=uuid.UUID(pack_id),
            content_md="v3 body — proposed candidate",
            files=None,
            created_by="evolver",
            creator_identity_id=None,
        )
        await db.commit()
        return pack_id, str(v3.id)


def _stub_verify_result(version_id_str: str) -> verifier_svc.VerificationResult:
    return verifier_svc.VerificationResult(
        version_id=uuid.UUID(version_id_str),
        status="accepted",
        old_score_avg=0.0,
        new_score_avg=1.0,
        score_delta=1.0,
        replayed_artifacts=4,
        threshold=0.05,
        duration_ms=42,
    )


async def test_verify_now_happy_path(async_client):
    headers, _ws_id = await _bootstrap(async_client)
    pack_id, version_id = await _create_pack_with_v2_proposed(async_client, headers)

    async def fake_verify(_db, *, workspace_id, version_id, request=None):
        _ = (workspace_id, request)
        return _stub_verify_result(str(version_id))

    with patch(
        "app.api.v1.skills_verify.verifier_svc.verify_skill_version",
        fake_verify,
    ):
        r = await async_client.post(
            f"/api/v1/skills/packs/{pack_id}/versions/{version_id}/verify-now",
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    assert body["score_delta"] == pytest.approx(1.0)
    assert body["replayed_artifacts"] == 4


async def test_verify_now_requires_workspace_admin(async_client):
    # Owner of WS A creates everything; a separate identity is invited
    # as a regular member and tries to call verify-now.
    headers_admin, ws_id = await _bootstrap(async_client)
    pack_id, version_id = await _create_pack_with_v2_proposed(async_client, headers_admin)

    member_email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    password = "member-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": member_email, "name": "Member", "password": password},
    )
    assert r.status_code == 201
    member_id = r.json()["identity"]["id"]
    r = await async_client.post(
        "/api/v1/auth/login",
        json={"email": member_email, "password": password},
    )
    member_token = r.json()["access_token"]

    # Add the member to the admin's workspace as a plain MEMBER role.
    from app.db.models.membership import MembershipStatus
    from app.db.models.role import BuiltinRole
    from app.db.session import get_session_factory
    from app.repositories.workspace import MembershipRepository

    factory = get_session_factory()
    async with factory() as db:
        await MembershipRepository(db).create(
            workspace_id=uuid.UUID(ws_id),
            identity_id=uuid.UUID(member_id),
            role=BuiltinRole.MEMBER.value,
            status=MembershipStatus.ACTIVE,
        )
        await db.commit()

    member_headers = {
        "Authorization": f"Bearer {member_token}",
        "X-Workspace-Id": ws_id,
    }
    r = await async_client.post(
        f"/api/v1/skills/packs/{pack_id}/versions/{version_id}/verify-now",
        headers=member_headers,
    )
    assert r.status_code == 403, r.text


async def test_validation_endpoint_returns_state_and_results(async_client):
    headers, _ws_id = await _bootstrap(async_client)
    pack_id, version_id = await _create_pack_with_v2_proposed(async_client, headers)

    # Persist a synthetic validation_results blob directly so we don't
    # depend on running verifier_svc.verify_skill_version end-to-end here.
    from app.db.session import get_session_factory
    from app.repositories.skill_pack_version import SkillPackVersionRepository

    factory = get_session_factory()
    async with factory() as db:
        repo = SkillPackVersionRepository(db)
        version = await repo.get(uuid.UUID(version_id))
        assert version is not None
        version.validation_results = {
            "status": "accepted",
            "score_delta": 0.42,
            "old_score_avg": 0.1,
            "new_score_avg": 0.52,
            "threshold": 0.05,
            "replayed_artifacts": 4,
        }
        await db.flush([version])
        await db.commit()

    r = await async_client.get(
        f"/api/v1/skills/packs/{pack_id}/versions/{version_id}/validation",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_id"] == version_id
    assert body["pack_id"] == pack_id
    assert body["state"] == "proposed"
    assert body["validation_results"]["status"] == "accepted"
    assert body["validation_results"]["score_delta"] == pytest.approx(0.42)


async def test_verify_now_rejects_unknown_pack(async_client):
    headers, _ = await _bootstrap(async_client)
    bogus_pack = str(uuid.uuid4())
    bogus_version = str(uuid.uuid4())
    r = await async_client.post(
        f"/api/v1/skills/packs/{bogus_pack}/versions/{bogus_version}/verify-now",
        headers=headers,
    )
    assert r.status_code == 404


async def test_verify_now_rate_limit_fires(async_client):
    headers, _ws_id = await _bootstrap(async_client)
    pack_id, version_id = await _create_pack_with_v2_proposed(async_client, headers)

    async def fake_verify(_db, *, workspace_id, version_id, request=None):
        _ = (workspace_id, request)
        return _stub_verify_result(str(version_id))

    statuses = []
    with patch(
        "app.api.v1.skills_verify.verifier_svc.verify_skill_version",
        fake_verify,
    ):
        for _ in range(7):
            r = await async_client.post(
                f"/api/v1/skills/packs/{pack_id}/versions/{version_id}/verify-now",
                headers=headers,
            )
            statuses.append(r.status_code)

    # First call may succeed (200) or 409 if state already advanced, but
    # at least one of the trailing calls must hit the 5/300s rate cap.
    # 422 is also acceptable if the version is no longer PROPOSED on
    # subsequent attempts.
    assert 429 in statuses, statuses
