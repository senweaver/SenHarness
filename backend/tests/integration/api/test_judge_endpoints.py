"""End-to-end tests for the M0.3 judge REST routes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"judge-api-{uuid.uuid4().hex[:8]}@example.com"
    password = "judge-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Judge Api", "password": password},
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
        json={"name": "Judge WS", "slug": f"judge-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _new_session(async_client, headers) -> str:
    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def _seed_judged_artifact(
    *,
    workspace_id: str,
    session_id: str,
    identity_id: str,
    score: int = 1,
    confidence: float = 0.9,
) -> str:
    from app.db.session import get_session_factory
    from app.services import judge as judge_svc
    from app.services import session_artifact as artifact_svc

    factory: async_sessionmaker = get_session_factory()
    async with factory() as db:
        artifact = await artifact_svc.capture_artifact(
            db,
            run_id=uuid.uuid4(),
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            user_text="seed-judged",
            events=[{"kind": "final", "data": {}}],
            final_outcome="success",
        )
        await judge_svc.persist_verdict(
            db,
            workspace_id=uuid.UUID(workspace_id),
            artifact_id=artifact.id,
            score=score,
            confidence=confidence,
            rationale="seeded verdict",
            judged_by_model="test:fake",
        )
        await db.commit()
        return str(artifact.id)


async def _seed_unjudged_artifact(
    *,
    workspace_id: str,
    session_id: str,
    identity_id: str,
) -> str:
    from app.db.session import get_session_factory
    from app.services import session_artifact as artifact_svc

    factory: async_sessionmaker = get_session_factory()
    async with factory() as db:
        artifact = await artifact_svc.capture_artifact(
            db,
            run_id=uuid.uuid4(),
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            user_text="seed-unjudged",
            events=[{"kind": "delta", "data": {"text": "x"}}],
            final_outcome="success",
        )
        await db.commit()
        return str(artifact.id)


# ─── Happy paths ─────────────────────────────────────────────
async def test_get_artifact_verdict_happy(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    aid = await _seed_judged_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    r = await async_client.get(
        f"/api/v1/artifacts/{aid}/verdict", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["artifact_id"] == aid
    assert body["score"] == 1
    assert body["judged_by_model"] == "test:fake"


async def test_get_verdict_404_when_unjudged(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    aid = await _seed_unjudged_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )
    r = await async_client.get(
        f"/api/v1/artifacts/{aid}/verdict", headers=headers
    )
    assert r.status_code == 404


async def test_session_judge_summary_counts(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    actor = _identity_id_from_token(headers)
    await _seed_judged_artifact(
        workspace_id=ws_id, session_id=sid, identity_id=actor, score=1
    )
    await _seed_judged_artifact(
        workspace_id=ws_id, session_id=sid, identity_id=actor, score=-1
    )
    await _seed_unjudged_artifact(
        workspace_id=ws_id, session_id=sid, identity_id=actor
    )

    r = await async_client.get(
        f"/api/v1/sessions/{sid}/artifacts/judge-summary", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_artifacts"] == 3
    assert body["success"] == 1
    assert body["failure"] == 1
    assert body["unjudged"] == 1


async def test_rejudge_resets_score_and_audits(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    aid = await _seed_judged_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    r = await async_client.post(
        f"/api/v1/artifacts/{aid}/rejudge", headers=headers
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["judge_score"] is None

    # Verdict row gone, score nulled.
    r = await async_client.get(
        f"/api/v1/artifacts/{aid}/verdict", headers=headers
    )
    assert r.status_code == 404

    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                        AuditEvent.action == "judge.rejudge_requested",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1


# ─── RBAC + isolation ────────────────────────────────────────
async def test_get_verdict_other_workspace_404(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    aid = await _seed_judged_artifact(
        workspace_id=ws_a,
        session_id=sid_a,
        identity_id=_identity_id_from_token(headers_a),
    )

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/artifacts/{aid}/verdict", headers=headers_b
    )
    assert r.status_code in (403, 404)


async def test_rejudge_unauthenticated_blocked(async_client):
    aid = uuid.uuid4()
    r = await async_client.post(f"/api/v1/artifacts/{aid}/rejudge")
    assert r.status_code in (401, 403)
