"""Integration: hallucination gate full loop (M2.5.1).

End-to-end coverage of the M2.5 dispatch handler integration:

* Aux LLM stub returns score 0.30 → child is parked in
  ``HALLUCINATION_REVIEW`` and a pending Approval is filed with
  ``resource_type='subagent_hallucination_review'``.
* Admin rejects via ``reject_approval`` → the spine row transitions
  to ``KILLED``, the Approval flips to DENIED, and the
  ``subagent.hallucination_rejected`` audit lands.
* A separate child runs the same flow but the admin approves via
  ``approve_approval`` → ``COMPLETED`` + ``subagent.hallucination_approved``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.audit import AuditEvent
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.db.session import get_session_factory
from app.services import approval as approval_svc
from app.services import subagent_run as subagent_svc

pytestmark = pytest.mark.asyncio


async def _make_workspace(async_client) -> str:
    email = f"hallucination-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Hallucination Tester",
            "password": "hallu-test-password-very-long",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["workspace"]["id"]


async def _register_and_park(
    *,
    ws_id: str,
    score: float,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Register a child, run the gate with stubbed aux, return ids."""

    async def fake_evaluate(db, *, workspace_id, final_output, timeout_s=25.0):
        return score, "stub:aux"

    async def fake_breaker_open(*, bucket, workspace_id, trip_at):
        return False

    monkeypatch.setattr(subagent_svc, "evaluate_hallucination", fake_evaluate)
    monkeypatch.setattr(
        "app.jobs._breaker.is_breaker_open", fake_breaker_open
    )

    factory = get_session_factory()
    async with factory() as db:
        child = await subagent_svc.register_run(
            db,
            workspace_id=uuid.UUID(ws_id),
            parent_run_id=uuid.uuid4(),
            child_run_id=uuid.uuid4(),
            spawn_depth=0,
        )
        outcome = await subagent_svc.gate_hallucination_or_approve(
            db,
            workspace_id=uuid.UUID(ws_id),
            child_run=child,
            final_output="Probably the answer is X.",
        )
        await db.commit()
    return child.id, outcome


async def test_hallucination_below_threshold_then_admin_rejects(
    async_client, monkeypatch
):
    ws_id = await _make_workspace(async_client)
    spine_id, outcome = await _register_and_park(
        ws_id=ws_id, score=0.30, monkeypatch=monkeypatch
    )
    assert outcome == "approval_required"

    factory = get_session_factory()
    async with factory() as db:
        spine = await db.get(SubAgentRun, spine_id)
        assert spine is not None
        assert spine.state == SubAgentRunState.HALLUCINATION_REVIEW
        approval_id = spine.hallucination_approval_id
        assert approval_id is not None

        # Admin rejects via the typed service entry-point; the
        # ``reject_approval`` hook should drive the spine row to
        # KILLED inside the same transaction.
        await approval_svc.reject_approval(
            db,
            approval_id=approval_id,
            workspace_id=uuid.UUID(ws_id),
            actor_identity_id=None,
            reason="reviewed: ungrounded",
        )
        await db.commit()

    async with factory() as db:
        spine = await db.get(SubAgentRun, spine_id)
        assert spine is not None
        assert spine.state == SubAgentRunState.KILLED
        approval = (
            await db.execute(
                select(Approval).where(Approval.id == approval_id)
            )
        ).scalar_one_or_none()
        assert approval is not None
        assert approval.status == ApprovalStatus.DENIED

        audit = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action
                    == subagent_svc.AUDIT_HALLUCINATION_REJECTED
                )
            )
        ).scalars().all()
        assert any(a.resource_id == spine_id for a in audit)


async def test_hallucination_below_threshold_then_admin_approves(
    async_client, monkeypatch
):
    ws_id = await _make_workspace(async_client)
    spine_id, outcome = await _register_and_park(
        ws_id=ws_id, score=0.20, monkeypatch=monkeypatch
    )
    assert outcome == "approval_required"

    factory = get_session_factory()
    async with factory() as db:
        spine = await db.get(SubAgentRun, spine_id)
        assert spine is not None
        approval_id = spine.hallucination_approval_id
        assert approval_id is not None

        await approval_svc.approve_approval(
            db,
            approval_id=approval_id,
            workspace_id=uuid.UUID(ws_id),
            actor_identity_id=None,
            reason="reviewed: looks good",
        )
        await db.commit()

    async with factory() as db:
        spine = await db.get(SubAgentRun, spine_id)
        assert spine is not None
        assert spine.state == SubAgentRunState.COMPLETED
        approval = (
            await db.execute(
                select(Approval).where(Approval.id == approval_id)
            )
        ).scalar_one_or_none()
        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED

        audit = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action
                    == subagent_svc.AUDIT_HALLUCINATION_APPROVED
                )
            )
        ).scalars().all()
        assert any(a.resource_id == spine_id for a in audit)
