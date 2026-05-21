"""ARQ task: ``judge_periodic_sweep`` — picks up unjudged artifacts.

We monkey-patch :func:`app.worker.queue.enqueue` to count enqueued
artifact ids instead of pushing to a real Redis (so the test stays
isolated from arq itself).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.asyncio


async def _bootstrap_session(async_client) -> tuple[dict, str, str]:
    email = f"judge-sweep-{uuid.uuid4().hex[:8]}@example.com"
    password = "judge-sweep-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Judge Sweep", "password": password},
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
        json={"name": "Judge Sweep WS", "slug": f"jsw-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    sid = r.json()["id"]
    return headers, ws_id, sid


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def _seed_aged_artifact(
    *, workspace_id: str, session_id: str, identity_id: str, final_outcome: str = "success"
) -> str:
    from app.core.security import utcnow_naive
    from app.db.session import get_session_factory
    from app.services import session_artifact as artifact_svc

    factory: async_sessionmaker = get_session_factory()
    aged = utcnow_naive() - timedelta(minutes=10)
    async with factory() as db:
        row = await artifact_svc.capture_artifact(
            db,
            run_id=uuid.uuid4(),
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            user_text="seed",
            events=[{"kind": "final", "data": {}}],
            final_outcome=final_outcome,
            finished_at=aged,
        )
        await db.commit()
        return str(row.id)


async def test_periodic_sweep_enqueues_unjudged(async_client):
    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_session(async_client)
    actor = _identity_id_from_token(headers)
    aids = []
    for _ in range(3):
        aids.append(
            await _seed_aged_artifact(
                workspace_id=ws_id, session_id=sid, identity_id=actor
            )
        )
    # One cancelled — must NOT be enqueued.
    cancelled_aid = await _seed_aged_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=actor,
        final_outcome="cancelled",
    )

    enqueued: list[str] = []

    async def fake_enqueue(function, *args, _defer_by=None, **kwargs):
        enqueued.append(args[0])
        return "job-fake"

    from unittest.mock import patch

    with patch("app.worker.queue.enqueue", fake_enqueue):
        result = await judge_mod.judge_periodic_sweep({})

    assert result["status"] == "swept"
    assert result["enqueued"] >= 3
    for aid in aids:
        assert aid in enqueued
    assert cancelled_aid not in enqueued


async def test_periodic_sweep_skips_degraded_workspace(
    async_client, redis_available
):
    if not redis_available:
        pytest.skip("Redis required for breaker state")

    from app.jobs import judge as judge_mod

    headers, ws_id, sid = await _bootstrap_session(async_client)
    actor = _identity_id_from_token(headers)
    await _seed_aged_artifact(
        workspace_id=ws_id, session_id=sid, identity_id=actor
    )

    for _ in range(6):
        await judge_mod.bump_failure(  # type: ignore[attr-defined]
            bucket=judge_mod._JUDGE_BUCKET,
            workspace_id=ws_id,
            window_seconds=300,
            recover_seconds=3600,
        )

    enqueued: list[str] = []

    async def fake_enqueue(function, *args, _defer_by=None, **kwargs):
        enqueued.append(args[0])
        return "job-fake"

    from unittest.mock import patch

    with patch("app.worker.queue.enqueue", fake_enqueue):
        result = await judge_mod.judge_periodic_sweep({})

    # The degraded workspace must contribute zero enqueues (other
    # workspaces with seeded data may still be processed by the sweep).
    assert result["skipped_degraded"] >= 1

    # Cleanup so the global Redis state doesn't leak into siblings.
    await judge_mod.reset_failure(  # type: ignore[attr-defined]
        bucket=judge_mod._JUDGE_BUCKET,
        workspace_id=ws_id,
    )
