"""ARQ task: ``judge_session_artifact`` end-to-end with a mocked aux LLM.

Covers:
* happy path → verdict persisted + score mirrored
* cancelled artifact → skipped + audit
* already judged → skipped + audit
* aux failure → bumps breaker, raises so ARQ retries
* breaker tripped → degraded path (score=0, audit ``judge.degraded``)
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    JudgeVerdict,
)

pytestmark = pytest.mark.asyncio


async def _bootstrap_workspace(async_client) -> tuple[dict, str, str]:
    email = f"judge-job-{uuid.uuid4().hex[:8]}@example.com"
    password = "judge-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Judge Job", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Judge WS", "slug": f"jjob-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    r = await async_client.post("/api/v1/sessions", headers=headers, json={"kind": "p2p"})
    sid = r.json()["id"]
    return headers, ws_id, sid


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def _seed_artifact(
    *,
    workspace_id: str,
    session_id: str,
    identity_id: str,
    final_outcome: str = "success",
) -> str:
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
            events=[{"kind": "final", "data": {"text": "ok"}}],
            final_outcome=final_outcome,
        )
        await db.commit()
        return str(row.id)


def _aux_config() -> AuxiliaryConfig:
    return AuxiliaryConfig(task=AuxiliaryTask.JUDGE, model="test:fake")


def _stub_get_aux(_db, *, workspace_id, task):
    _ = (workspace_id, task)
    return _aux_config()


async def test_judge_session_artifact_happy_path(async_client):
    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    async def fake_judge(_db, *, workspace_id, artifact, turns_serialized, **kwargs):
        return (
            JudgeVerdict(
                score=1,
                confidence=0.92,
                rationale="solid run",
                process_notes=["one tool"],
            ),
            _aux_config(),
        )

    await judge_mod.reset_failure(  # type: ignore[attr-defined]
        bucket=judge_mod._JUDGE_BUCKET,
        workspace_id=ws_id,
    )

    with patch.object(judge_mod, "call_aux_judge", fake_judge):
        result = await judge_mod.judge_session_artifact({}, aid)

    assert result["status"] == "scored"
    assert result["score"] == 1
    assert result["judged_by_model"] == "test:fake"

    r = await async_client.get(f"/api/v1/artifacts/{aid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["judge_score"] == pytest.approx(1.0)


async def test_judge_session_artifact_skips_cancelled(async_client):
    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
        final_outcome="cancelled",
    )

    result = await judge_mod.judge_session_artifact({}, aid)
    assert result["status"] == "skipped_cancelled"

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
                        AuditEvent.action == "judge.skipped_cancelled",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1


async def test_judge_session_artifact_skips_already_judged(async_client):
    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    from app.db.session import get_session_factory
    from app.services import judge as judge_svc

    factory = get_session_factory()
    async with factory() as db:
        await judge_svc.persist_verdict(
            db,
            workspace_id=uuid.UUID(ws_id),
            artifact_id=uuid.UUID(aid),
            score=1,
            confidence=0.8,
            rationale="prior verdict",
        )
        await db.commit()

    result = await judge_mod.judge_session_artifact({}, aid)
    assert result["status"] == "skipped_already"


async def test_judge_session_artifact_aux_failure_bumps_and_raises(async_client, redis_available):
    if not redis_available:
        pytest.skip("Redis required for breaker counter")

    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    async def fake_judge(_db, *, workspace_id, artifact, turns_serialized, **kwargs):
        return None, _aux_config()

    await judge_mod.reset_failure(  # type: ignore[attr-defined]
        bucket=judge_mod._JUDGE_BUCKET,
        workspace_id=ws_id,
    )

    with (
        patch.object(judge_mod, "call_aux_judge", fake_judge),
        pytest.raises(RuntimeError),
    ):
        await judge_mod.judge_session_artifact({}, aid)


async def test_judge_session_artifact_breaker_open_writes_degraded(async_client, redis_available):
    if not redis_available:
        pytest.skip("Redis required for breaker state")

    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    aid = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=_identity_id_from_token(headers),
    )

    # Manually trip the breaker by incrementing the failure key past
    # the configured strikes. We use the same helper the job uses so
    # the test stays independent of the in-test bookkeeping format.
    for _ in range(6):
        await judge_mod.bump_failure(  # type: ignore[attr-defined]
            bucket=judge_mod._JUDGE_BUCKET,
            workspace_id=ws_id,
            window_seconds=300,
            recover_seconds=3600,
        )

    result = await judge_mod.judge_session_artifact({}, aid)
    assert result["status"] == "degraded"

    r = await async_client.get(f"/api/v1/artifacts/{aid}", headers=headers)
    body = r.json()
    assert body["judge_score"] == pytest.approx(0.0)

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
                        AuditEvent.action == "judge.degraded",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1

    # Cleanup so subsequent tests in the same session don't see the
    # tripped breaker.
    await judge_mod.reset_failure(  # type: ignore[attr-defined]
        bucket=judge_mod._JUDGE_BUCKET,
        workspace_id=ws_id,
    )
