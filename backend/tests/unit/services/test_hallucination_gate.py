"""Unit tests for the hallucination gate (M2.5.1).

Exercises the three branches of :func:`gate_hallucination_or_approve`:

* score >= threshold → COMPLETED + ``subagent.hallucination_passed`` audit
* score < threshold → HALLUCINATION_REVIEW + Approval row filed
* breaker open → fail-open (COMPLETED + audit reason ``breaker_open``)

The aux LLM call is monkeypatched so the test stays fully offline; the
breaker primitives in :mod:`app.jobs._breaker` already fail-open when
Redis is unreachable, so this suite skips Redis as well as DB when
the matching containers aren't available.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.audit import AuditEvent
from app.db.models.subagent_run import SubAgentRunState
from app.services import subagent_run as svc

pytestmark = pytest.mark.asyncio


async def _make_child(db_session, workspace) -> svc.SubAgentRun:
    return await svc.register_run(
        db_session,
        workspace_id=workspace.id,
        parent_run_id=uuid.uuid4(),
        child_run_id=uuid.uuid4(),
        spawn_depth=0,
        retry_budget=3,
    )


async def test_gate_passes_when_score_above_threshold(
    db_session, workspace, monkeypatch
):
    child = await _make_child(db_session, workspace)

    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        return 0.82, "fake:gpt"

    monkeypatch.setattr(svc, "evaluate_hallucination", fake_evaluate)

    outcome = await svc.gate_hallucination_or_approve(
        db_session,
        workspace_id=workspace.id,
        child_run=child,
        final_output="The OKR doc lists Q3 NPS=58 (cited).",
    )
    assert outcome == "passed"

    refreshed = await db_session.get(svc.SubAgentRun, child.id)
    assert refreshed.state == SubAgentRunState.COMPLETED
    assert refreshed.hallucination_score == pytest.approx(0.82)

    audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == svc.AUDIT_HALLUCINATION_PASSED,
                AuditEvent.resource_id == child.id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None


async def test_gate_files_approval_when_score_below_threshold(
    db_session, workspace, monkeypatch
):
    child = await _make_child(db_session, workspace)

    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        return 0.30, "fake:gpt"

    monkeypatch.setattr(svc, "evaluate_hallucination", fake_evaluate)

    outcome = await svc.gate_hallucination_or_approve(
        db_session,
        workspace_id=workspace.id,
        child_run=child,
        final_output="Probably the answer is X.",
    )
    assert outcome == "approval_required"

    refreshed = await db_session.get(svc.SubAgentRun, child.id)
    assert refreshed.state == SubAgentRunState.HALLUCINATION_REVIEW
    assert refreshed.hallucination_score == pytest.approx(0.30)
    assert refreshed.hallucination_approval_id is not None

    approval = (
        await db_session.execute(
            select(Approval).where(
                Approval.id == refreshed.hallucination_approval_id
            )
        )
    ).scalar_one_or_none()
    assert approval is not None
    assert approval.resource_type == svc.HALLUCINATION_RESOURCE_TYPE
    assert approval.status == ApprovalStatus.PENDING
    assert approval.expires_at is not None
    body = approval.tool_args
    assert body["child_run_id"] == str(child.child_run_id)
    assert body["score"] == pytest.approx(0.30)


async def test_gate_fails_open_when_breaker_open(
    db_session, workspace, monkeypatch
):
    child = await _make_child(db_session, workspace)

    async def fake_breaker_open(*, bucket, workspace_id, trip_at):
        assert bucket == svc.HALLUCINATION_BREAKER_BUCKET
        return True

    # Aux call should NEVER fire when the breaker is open.
    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        raise AssertionError("evaluate must not be called when breaker open")

    monkeypatch.setattr(
        "app.jobs._breaker.is_breaker_open", fake_breaker_open
    )
    monkeypatch.setattr(svc, "evaluate_hallucination", fake_evaluate)

    outcome = await svc.gate_hallucination_or_approve(
        db_session,
        workspace_id=workspace.id,
        child_run=child,
        final_output="anything",
    )
    assert outcome == "passed"

    refreshed = await db_session.get(svc.SubAgentRun, child.id)
    assert refreshed.state == SubAgentRunState.COMPLETED
    assert refreshed.hallucination_score is None

    # Audit row should record the fail-open reason.
    audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == svc.AUDIT_HALLUCINATION_PASSED,
                AuditEvent.resource_id == child.id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.metadata_json.get("reason") == "breaker_open"


async def test_gate_fails_open_and_bumps_breaker_on_eval_exception(
    db_session, workspace, monkeypatch
):
    child = await _make_child(db_session, workspace)

    bumps: list[tuple[str, str]] = []

    async def fake_breaker_open(*, bucket, workspace_id, trip_at):
        return False

    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        raise RuntimeError("aux exploded")

    async def fake_bump(*, bucket, workspace_id, window_seconds, recover_seconds=None):
        bumps.append((bucket, workspace_id))
        return 1

    monkeypatch.setattr(
        "app.jobs._breaker.is_breaker_open", fake_breaker_open
    )
    monkeypatch.setattr("app.jobs._breaker.bump_failure", fake_bump)
    monkeypatch.setattr(svc, "evaluate_hallucination", fake_evaluate)

    outcome = await svc.gate_hallucination_or_approve(
        db_session,
        workspace_id=workspace.id,
        child_run=child,
        final_output="anything",
    )
    assert outcome == "passed"

    assert bumps == [(svc.HALLUCINATION_BREAKER_BUCKET, str(workspace.id))]
    refreshed = await db_session.get(svc.SubAgentRun, child.id)
    assert refreshed.state == SubAgentRunState.COMPLETED
