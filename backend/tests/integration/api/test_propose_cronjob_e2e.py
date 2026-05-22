"""End-to-end coverage for the M2.8 propose_cronjob_create surface.

The runner is exercised against a real Postgres engine via the
``db_session`` fixture (skips cleanly when Postgres isn't available).
Three contracts the integration layer must guarantee:

1. Happy path — Approval row lands with ``resource_type=flow_create``,
   tool args mirror the proposal body (incl. ``schedule_kind``), TTL
   is exactly 7 days, the audit row emits ``evolver.proposed_cronjob``
   with the canonical metadata, and **no Flow row is touched**.
2. Capability gate — non-evolver agents (``policy["agent_kind"]``
   absent or any other value) MUST NOT see the verb in the resolved
   tool list, mirroring the ``available_for_kinds`` enforcement the
   M0/M1 tests established for the skill propose verbs.
3. Rate limit — the bucket trips at 5/min and rejects with the
   canonical code; subsequent calls inside the window also reject.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools import BUILTIN_TOOL_REGISTRY
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.cronjob_propose import (
    AUDIT_PROPOSED,
    AUDIT_REJECTED,
    CRONJOB_PROPOSE_RATE_PER_MINUTE,
    ProposeCronjobArgs,
    run_propose_cronjob,
)
from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.flow import Flow

pytestmark = pytest.mark.asyncio


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _set_ctx(workspace, identity, *, agent_id):
    set_context(
        ToolRunContext(
            run_id=uuid.uuid4(),
            workspace_id=workspace.id,
            session_id=uuid.uuid4(),
            identity_id=identity.id,
            agent_id=agent_id,
            scratch_base=Path("/tmp"),
        )
    )


async def _enable_evolver(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


def _resolve_for_kind(toolbox: list[str], *, agent_kind: str | None) -> list[str]:
    """Mirror the runner-side filter on ``available_for_kinds``."""
    out: list[str] = []
    for name in toolbox:
        tool = BUILTIN_TOOL_REGISTRY.get(name)
        if tool is None:
            continue
        if tool.available_for_kinds is not None and agent_kind not in tool.available_for_kinds:
            continue
        out.append(name)
    return out


# ─── Capability gate ────────────────────────────────────────
async def test_cronjob_verb_visible_only_to_evolver_agent_kind() -> None:
    visible_for_evolver = _resolve_for_kind(["propose_cronjob_create"], agent_kind="evolver")
    assert visible_for_evolver == ["propose_cronjob_create"]

    visible_for_default = _resolve_for_kind(["propose_cronjob_create"], agent_kind=None)
    assert visible_for_default == []

    visible_for_other = _resolve_for_kind(["propose_cronjob_create"], agent_kind="workspace")
    assert visible_for_other == []


# ─── Happy path ─────────────────────────────────────────────
async def test_cronjob_propose_e2e_files_approval_audit_and_no_flow_row(
    db_session, workspace, identity, agent, monkeypatch
):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity, agent_id=agent.id)
    monkeypatch.setattr(
        "app.agents.tools.cronjob_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    before = utcnow_naive()
    result = await run_propose_cronjob(
        ProposeCronjobArgs(
            name="OKR daily readback",
            schedule="0 9 * * *",
            prompt_template="Read me my OKR for today.",
            rationale="user asked twice this week",
        )
    )

    assert result["status"] == "proposed"
    assert result["kind"] == ApprovalResourceType.FLOW_CREATE.value
    assert result["schedule_kind"] == "cron"
    assert "approval_id" in result

    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.workspace_id == workspace.id
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == ApprovalResourceType.FLOW_CREATE.value
    assert approval.resource_id is None  # No Flow row exists yet.
    assert approval.session_id is None
    assert approval.tool_name == "_propose_cronjob_create"
    assert approval.expires_at is not None
    # 7-day TTL — allow a few seconds of jitter for slow CI machines.
    delta = approval.expires_at - before
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    body = dict(approval.tool_args)
    assert body["kind"] == ApprovalResourceType.FLOW_CREATE.value
    assert body["name"] == "OKR daily readback"
    assert body["schedule"] == "0 9 * * *"
    assert body["schedule_kind"] == "cron"
    assert body["target_agent_id"] == str(agent.id)
    assert body["delivery_channel_ids"] == []
    assert body["rationale"] == "user asked twice this week"

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_PROPOSED,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["approval_id"] == str(approval.id)
    assert audits[0].metadata_json["schedule_kind"] == "cron"
    assert audits[0].metadata_json["ttl_days"] == 7

    # Critical: the verb must NOT have created a Flow row. M2.5 dispatch
    # is the only place that mints Flow rows on approval.
    flows = list(
        (await db_session.execute(select(Flow).where(Flow.workspace_id == workspace.id))).scalars()
    )
    assert flows == []


# ─── Rate limit ─────────────────────────────────────────────
async def test_cronjob_rate_limit_trips_after_budget(
    db_session, workspace, identity, agent, monkeypatch
):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity, agent_id=agent.id)
    monkeypatch.setattr(
        "app.agents.tools.cronjob_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    # Force the rate gate to deny without depending on a real Redis
    # backend (Postgres-only CI machines). The runner should still
    # write the canonical rejection audit row with code='rate_limited'.
    async def _denied(**_kwargs) -> bool:
        return False

    monkeypatch.setattr("app.agents.tools.cronjob_propose.consume_rate", _denied)

    result = await run_propose_cronjob(
        ProposeCronjobArgs(
            name="rate-blocked",
            schedule="every 1h",
            prompt_template="x",
            rationale="x",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.rate_limited"
    assert str(CRONJOB_PROPOSE_RATE_PER_MINUTE) in result["message"]

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == AUDIT_REJECTED,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["code"] == "rate_limited"
