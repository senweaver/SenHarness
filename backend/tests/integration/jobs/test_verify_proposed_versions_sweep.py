"""Integration: ``verify_proposed_versions_sweep`` (M2.4).

Spins up enough infrastructure to walk one workspace end-to-end:

* A workspace with ``evolver.auto_verifier.enabled=True``.
* Five PROPOSED SkillPackVersion rows.
* A patched ``verify_skill_version`` that records calls + returns
  alternating accept / reject statuses so we can verify the sweep
  fans out to every row + correctly bumps the per-status counters.
* A second test that flips the workspace breaker and asserts the
  sweep short-circuits the workspace without touching the verifier
  at all.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import skill_verifier as verifier_svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"verify-sweep-{uuid.uuid4().hex[:8]}@example.com"
    password = "verifier-sweep-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Verifier Sweep", "password": password},
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


async def _enable_auto_verifier(workspace_id: str) -> None:
    """Force ``evolver.auto_verifier.enabled=True`` on the workspace.

    The default workspace ships with ``evolver.enabled=False``, but the
    auto_verifier sub-block defaults to ``True`` in the schema. The
    sweep only walks workspaces where the merged config returns
    ``auto_verifier.enabled is True``, which is already the case for
    fresh workspaces. We still set it explicitly so the test isn't
    sensitive to a future default flip.
    """
    from app.db.session import get_session_factory
    from app.repositories.workspace import WorkspaceRepository

    factory: async_sessionmaker = get_session_factory()
    async with factory() as db:
        repo = WorkspaceRepository(db)
        ws = await repo.get(uuid.UUID(workspace_id))
        if ws is None:
            return
        home = dict(ws.home_config_json or {})
        evolver = dict(home.get("evolver") or {})
        evolver["enabled"] = True
        evolver["auto_verifier"] = {
            **(evolver.get("auto_verifier") or {}),
            "enabled": True,
            "min_score_delta": 0.05,
            "min_replay_artifacts": 3,
        }
        home["evolver"] = evolver
        ws.home_config_json = home
        await db.flush([ws])
        await db.commit()


async def _seed_proposed_versions(workspace_id: str, identity_id: str, *, count: int) -> list[str]:
    from app.db.session import get_session_factory
    from app.repositories.skills import SkillPackRepository
    from app.services import skill_version as svc

    factory: async_sessionmaker = get_session_factory()
    ids: list[str] = []
    async with factory() as db:
        for i in range(count):
            pack = await SkillPackRepository(db).create(
                workspace_id=uuid.UUID(workspace_id),
                slug=f"verify-{uuid.uuid4().hex[:6]}-{i}",
                name=f"Verify pack {i}",
                description="x",
                version="0.1.0",
                publisher=None,
                signature=None,
                manifest_json={},
                enabled=True,
                metadata_json={},
                created_by=uuid.UUID(identity_id),
            )
            v1 = await svc.create_version(
                db,
                workspace_id=uuid.UUID(workspace_id),
                pack_id=pack.id,
                content_md=f"v1 body {i}",
                files=None,
                created_by="user",
                creator_identity_id=uuid.UUID(identity_id),
            )
            await svc.activate_version(
                db,
                workspace_id=uuid.UUID(workspace_id),
                version_id=v1.id,
                actor_identity_id=uuid.UUID(identity_id),
                reason="seed",
            )
            v2 = await svc.create_version(
                db,
                workspace_id=uuid.UUID(workspace_id),
                pack_id=pack.id,
                content_md=f"v2 body {i}",
                files=None,
                created_by="evolver",
                creator_identity_id=uuid.UUID(identity_id),
            )
            ids.append(str(v2.id))
        await db.commit()
    return ids


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def test_sweep_processes_all_proposed_versions(async_client):
    from app.jobs import skill_verify as sweep_mod

    headers, ws_id = await _bootstrap(async_client)
    await _enable_auto_verifier(ws_id)
    actor = _identity_id_from_token(headers)
    version_ids = await _seed_proposed_versions(ws_id, actor, count=5)

    statuses_returned: list[str] = []
    seen_version_ids: list[str] = []

    async def fake_verify(_db, *, workspace_id, version_id, request=None):
        _ = (workspace_id, request)
        seen_version_ids.append(str(version_id))
        # Alternate accept / reject / accept / reject / accept so the
        # counters land at 3 accepted + 2 rejected.
        idx = len(statuses_returned)
        status = "accepted" if idx % 2 == 0 else "rejected"
        statuses_returned.append(status)
        return verifier_svc.VerificationResult(
            version_id=version_id,
            status=status,
            old_score_avg=0.0,
            new_score_avg=1.0 if status == "accepted" else 0.0,
            score_delta=1.0 if status == "accepted" else -1.0,
            replayed_artifacts=3,
            threshold=0.05,
            duration_ms=10,
        )

    # Reset the breaker so the workspace isn't skipped from a prior sibling test.
    from app.jobs._breaker import reset_failure

    await reset_failure(bucket=verifier_svc.VerifierBreakerBucket, workspace_id=ws_id)

    with patch.object(sweep_mod, "verify_skill_version", fake_verify):
        result = await sweep_mod.verify_proposed_versions_sweep({})

    assert result["versions_verified"] >= 5
    assert result["versions_accepted"] >= 3
    assert result["versions_rejected"] >= 2
    for vid in version_ids:
        assert vid in seen_version_ids


async def test_sweep_skips_workspace_when_breaker_open(async_client, redis_available):
    if not redis_available:
        pytest.skip("Redis required for breaker state")

    from app.jobs import skill_verify as sweep_mod
    from app.jobs._breaker import bump_failure, reset_failure

    headers, ws_id = await _bootstrap(async_client)
    await _enable_auto_verifier(ws_id)
    actor = _identity_id_from_token(headers)
    await _seed_proposed_versions(ws_id, actor, count=2)

    for _ in range(5):
        await bump_failure(
            bucket=verifier_svc.VerifierBreakerBucket,
            workspace_id=ws_id,
            window_seconds=600,
            recover_seconds=1800,
        )

    invocations: list[str] = []

    async def fake_verify(_db, *, workspace_id, version_id, request=None):
        invocations.append(str(version_id))
        return verifier_svc.VerificationResult(
            version_id=version_id,
            status="accepted",
            old_score_avg=0.0,
            new_score_avg=1.0,
            score_delta=1.0,
            replayed_artifacts=3,
            threshold=0.05,
            duration_ms=10,
        )

    try:
        with patch.object(sweep_mod, "verify_skill_version", fake_verify):
            result = await sweep_mod.verify_proposed_versions_sweep({})

        assert result["workspaces_skipped_breaker"] >= 1
        # The breaker-tripped workspace must not have invoked the verifier.
        # Other workspaces with proposed versions may still be visible to
        # the sweep, so we can't assert ``invocations == []`` globally —
        # we only assert this workspace's versions weren't touched.
        from app.db.session import get_session_factory

        factory: async_sessionmaker = get_session_factory()
        async with factory() as db:
            from sqlalchemy import select

            from app.db.models.skill_pack_version import (
                SkillPackVersion,
                SkillPackVersionState,
            )

            rows = (
                (
                    await db.execute(
                        select(SkillPackVersion.id).where(
                            SkillPackVersion.workspace_id == uuid.UUID(ws_id),
                            SkillPackVersion.state == SkillPackVersionState.PROPOSED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            this_ws_versions = {str(r) for r in rows}

        assert not (this_ws_versions & set(invocations)), (
            "breaker-open workspace must not have its versions verified"
        )
    finally:
        await reset_failure(bucket=verifier_svc.VerifierBreakerBucket, workspace_id=ws_id)
