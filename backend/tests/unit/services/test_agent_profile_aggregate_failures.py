"""Unit: ``agent_profile.aggregate_failure_modes`` (M3.4).

Two angles:

1. Aux LLM clusters are stitched into the persisted JSONB shape.
2. Breaker open / no aux model / aux failure all degrade to a
   heuristic baseline + ``aux_skipped=True``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask
from app.core.security import utcnow_naive
from app.db.models.judge_verdict import JudgeVerdict
from app.db.models.session_artifact import SessionArtifact
from app.schemas.session_artifact import ArtifactOutcome
from app.services import agent_profile as svc

pytestmark = pytest.mark.asyncio


async def _ensure_session(db, *, workspace_id, identity_id) -> uuid.UUID:
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db,
        workspace_id=workspace_id,
        owner_identity_id=identity_id,
        title="failure test",
    )
    await db.flush()
    return sess.id


async def _seed_failing_artifact(
    db,
    *,
    workspace_id,
    session_id,
    agent_id,
    error_kind: str,
    invoked_tools: list[str] | None = None,
    finished_at=None,
    judge_score=-1.0,
    process_notes: list[str] | None = None,
) -> SessionArtifact:
    art = SessionArtifact(
        workspace_id=workspace_id,
        run_id=uuid.uuid4(),
        session_id=session_id,
        agent_id=agent_id,
        identity_id=None,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=invoked_tools or [],
        iteration_count=1,
        final_outcome=ArtifactOutcome.ERROR.value,
        error_kind=error_kind,
        judge_score=judge_score,
        goal_alignment_avg=None,
        finished_at=finished_at or utcnow_naive(),
    )
    db.add(art)
    await db.flush([art])

    if process_notes is not None:
        verdict = JudgeVerdict(
            workspace_id=workspace_id,
            artifact_id=art.id,
            score=int(judge_score),
            confidence=0.9,
            rationale="failed",
            process_notes_json=list(process_notes),
            error_kind_hint=error_kind,
            judged_by_model="aux:test",
            latency_ms=12,
            degraded=False,
        )
        db.add(verdict)
        await db.flush([verdict])

    return art


async def test_aux_clusters_are_stitched(db_session, workspace, agent, identity, monkeypatch):
    sess = await _ensure_session(db_session, workspace_id=workspace.id, identity_id=identity.id)
    now = utcnow_naive()
    for i in range(4):
        await _seed_failing_artifact(
            db_session,
            workspace_id=workspace.id,
            session_id=sess,
            agent_id=agent.id,
            error_kind="rate_limit",
            invoked_tools=["browser"],
            finished_at=now - timedelta(hours=i),
            process_notes=[f"hit 429 at step {i}"],
        )

    captured: dict[str, Any] = {}

    async def fake_get_aux_model(db, *, workspace_id, task):
        _ = db, workspace_id, task
        return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="t:m")

    async def fake_call_aux_chat(*, config, system, user, response_format, timeout_s):
        _ = config, system, timeout_s
        captured["user"] = user
        return response_format(
            hallucination_kinds=[{"kind": "tool_arg_invented", "count": 3}],
            common_errors=[{"error_kind": "rate_limit", "count": 4}],
            error_patterns=[
                {
                    "pattern_summary": "Forgets to back off when 429 returned",
                    "frequency": 4,
                }
            ],
        )

    monkeypatch.setattr(svc, "get_aux_model", fake_get_aux_model)
    monkeypatch.setattr(svc, "call_aux_chat", fake_call_aux_chat)

    # is_breaker_open is module-level — patch so this case never trips.
    async def fake_breaker(**_):
        return False

    monkeypatch.setattr(svc, "is_breaker_open", fake_breaker)

    out = await svc.aggregate_failure_modes(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=now - timedelta(days=30),
    )

    assert out.aux_skipped is False
    assert out.artifacts_examined == 4
    fm = out.failure_modes
    assert fm["hallucination_kinds"][0]["kind"] == "tool_arg_invented"
    assert fm["common_errors"][0]["error_kind"] == "rate_limit"
    assert fm["error_patterns"][0]["pattern_summary"].startswith("Forgets to back off")
    assert "rate_limit" in captured["user"]


async def test_breaker_open_skips_aux(db_session, workspace, agent, identity, monkeypatch):
    sess = await _ensure_session(db_session, workspace_id=workspace.id, identity_id=identity.id)
    now = utcnow_naive()
    for i in range(3):
        await _seed_failing_artifact(
            db_session,
            workspace_id=workspace.id,
            session_id=sess,
            agent_id=agent.id,
            error_kind="auth",
            finished_at=now - timedelta(hours=i),
        )

    aux_calls = {"count": 0}

    async def fake_breaker(**_):
        return True

    async def fake_get_aux_model(*args, **kwargs):
        aux_calls["count"] += 1
        return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="t:m")

    async def fake_call_aux_chat(**_):
        aux_calls["count"] += 1
        return None

    monkeypatch.setattr(svc, "is_breaker_open", fake_breaker)
    monkeypatch.setattr(svc, "get_aux_model", fake_get_aux_model)
    monkeypatch.setattr(svc, "call_aux_chat", fake_call_aux_chat)

    out = await svc.aggregate_failure_modes(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=now - timedelta(days=30),
    )
    assert out.aux_skipped is True
    assert out.aux_skip_reason == "breaker_open"
    # Aux must not have been touched at all when breaker is open.
    assert aux_calls["count"] == 0
    # Heuristic baseline still produced ``common_errors`` from the
    # raw artifacts, so the row stays useful even on degrade.
    assert any(entry["error_kind"] == "auth" for entry in out.failure_modes["common_errors"])
    assert out.failure_modes["hallucination_kinds"] == []
    assert out.failure_modes["error_patterns"] == []


async def test_no_aux_model_falls_through(db_session, workspace, agent, identity, monkeypatch):
    sess = await _ensure_session(db_session, workspace_id=workspace.id, identity_id=identity.id)
    now = utcnow_naive()
    for i in range(2):
        await _seed_failing_artifact(
            db_session,
            workspace_id=workspace.id,
            session_id=sess,
            agent_id=agent.id,
            error_kind="parse_error",
            finished_at=now - timedelta(hours=i),
        )

    async def fake_breaker(**_):
        return False

    async def fake_get_aux_model(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "is_breaker_open", fake_breaker)
    monkeypatch.setattr(svc, "get_aux_model", fake_get_aux_model)

    out = await svc.aggregate_failure_modes(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        since=now - timedelta(days=30),
    )
    assert out.aux_skipped is True
    assert out.aux_skip_reason == "no_aux_model"
