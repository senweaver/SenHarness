"""ARQ job: ``score_message_alignment`` end-to-end with a mocked aux LLM.

The job opens its own DB session (via the global session factory) so we
seed via the public REST API instead of the ``db_session`` fixture —
otherwise the fixture's open transaction wouldn't be visible to the
job's session. Aux model + chat are patched so no real provider call
happens.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask
from app.jobs.judge import score_message_alignment

pytestmark = pytest.mark.asyncio


async def _bootstrap_with_goal(async_client) -> dict[str, str]:
    email = f"judge-{uuid.uuid4().hex[:8]}@example.com"
    password = "judge-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Judge Tester", "password": password},
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
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id

    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    assert r.status_code in (200, 201)
    session_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/sessions/{session_id}/goals",
        headers=headers,
        json={"goal_text": "ship M0.1", "alignment_threshold": 0.6},
    )
    assert r.status_code == 201
    goal_id = r.json()["id"]

    r = await async_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        headers=headers,
        json={"role": "assistant", "content_json": {"text": "an assistant reply"}},
    )
    assert r.status_code == 201
    message_id = r.json()["id"]

    return {
        "headers": headers,
        "workspace_id": ws_id,
        "session_id": session_id,
        "goal_id": goal_id,
        "message_id": message_id,
    }


def _aux_config() -> AuxiliaryConfig:
    return AuxiliaryConfig(
        task=AuxiliaryTask.GOAL_ALIGNMENT,
        model="test:fake",
    )


async def test_aux_high_score_persists_unflagged(async_client):
    from app.jobs import judge as judge_mod

    ctx_ids = await _bootstrap_with_goal(async_client)

    async def fake_get_aux_model(_db, *, workspace_id, task):
        return _aux_config()

    async def fake_call_aux_chat(*, config, system, user, response_format=None, timeout_s=25.0):
        return judge_mod._AlignmentResponse(score=0.92, rationale="advances goal")

    with (
        patch.object(judge_mod, "get_aux_model", fake_get_aux_model),
        patch.object(judge_mod, "call_aux_chat", fake_call_aux_chat),
    ):
        result = await score_message_alignment(
            {}, ctx_ids["goal_id"], ctx_ids["message_id"]
        )

    assert result["status"] == "scored"
    assert result["score"] == pytest.approx(0.92)
    assert result["flagged"] is False

    r = await async_client.get(
        f"/api/v1/sessions/{ctx_ids['session_id']}/alignment",
        headers=ctx_ids["headers"],
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(0.92)
    assert rows[0]["flagged"] is False
    assert rows[0]["judged_by_model"] == "test:fake"


async def test_aux_low_score_flags_and_audits(async_client):
    from app.jobs import judge as judge_mod

    ctx_ids = await _bootstrap_with_goal(async_client)

    async def fake_get_aux_model(_db, *, workspace_id, task):
        return _aux_config()

    async def fake_call_aux_chat(*, config, system, user, response_format=None, timeout_s=25.0):
        return judge_mod._AlignmentResponse(score=0.2, rationale="off topic")

    with (
        patch.object(judge_mod, "get_aux_model", fake_get_aux_model),
        patch.object(judge_mod, "call_aux_chat", fake_call_aux_chat),
    ):
        result = await score_message_alignment(
            {}, ctx_ids["goal_id"], ctx_ids["message_id"]
        )
    assert result["flagged"] is True

    # Verify the audit breadcrumb landed for low-alignment.
    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    async with get_session_factory()() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.workspace_id == uuid.UUID(ctx_ids["workspace_id"]))
                    .where(AuditEvent.action == "goal.alignment_low")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1


async def test_three_consecutive_failures_trip_breaker(
    async_client, redis_available
):
    if not redis_available:
        pytest.skip("Redis required for breaker counter")

    from app.jobs import judge as judge_mod

    ctx_ids = await _bootstrap_with_goal(async_client)

    async def fake_get_aux_model(_db, *, workspace_id, task):
        return _aux_config()

    async def boom(*, config, system, user, response_format=None, timeout_s=25.0):
        return None

    await judge_mod._reset_failure_counter(ctx_ids["workspace_id"])

    with (
        patch.object(judge_mod, "get_aux_model", fake_get_aux_model),
        patch.object(judge_mod, "call_aux_chat", boom),
    ):
        with pytest.raises(RuntimeError):
            await score_message_alignment({}, ctx_ids["goal_id"], ctx_ids["message_id"])
        with pytest.raises(RuntimeError):
            await score_message_alignment({}, ctx_ids["goal_id"], ctx_ids["message_id"])
        result = await score_message_alignment(
            {}, ctx_ids["goal_id"], ctx_ids["message_id"]
        )
        assert result["status"] == "scored"
        assert result["degraded"] is True
        assert result["judged_by_model"] == "heuristic:breaker"

    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    async with get_session_factory()() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.workspace_id == uuid.UUID(ctx_ids["workspace_id"]))
                    .where(AuditEvent.action == "judge.degraded")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1


async def test_unknown_goal_returns_skipped(async_client):
    """Random ids must short-circuit instead of raising."""
    from app.jobs import judge as judge_mod

    _ = async_client  # Pulls in _migrated_engine + redis_available.

    async def fake_get_aux_model(_db, *, workspace_id, task):
        return _aux_config()

    with patch.object(judge_mod, "get_aux_model", fake_get_aux_model):
        result = await score_message_alignment(
            {}, str(uuid.uuid4()), str(uuid.uuid4())
        )
    assert result["status"] == "skipped_goal_missing"
