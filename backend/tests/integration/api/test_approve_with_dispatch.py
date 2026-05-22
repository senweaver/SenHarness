"""Integration: ``POST /approvals/{id}/decision`` with M2.5 dispatch.

Three scenarios:

1. Approve a ``skill_pack_create`` proposal → pack flips to ACTIVE,
   version flips to ACTIVE, breaker reset, ``ApprovalDecisionResponse``
   carries the dispatch result.
2. Approve a ``flow_create`` proposal → Flow row lands enabled=False
   per the second-gate invariant.
3. Approve a malformed proposal → 409 with
   ``code='approval.dispatch_invalid_body'`` and the row stays
   pending.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.flow import Flow
from app.db.models.skill_pack_version import (
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.skills import (
    SkillPack,
    SkillPackSource,
    SkillPackState,
)
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _register_admin(async_client) -> tuple[dict, str]:
    email = f"app-{uuid.uuid4().hex[:8]}@example.com"
    password = "approve-dispatch-test-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Approver", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        token = r.json()["access_token"]
    workspace = body["workspace"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": workspace["id"],
    }
    return headers, workspace["id"]


async def _seed_skill_create_approval(
    *, ws_id: str, identity_id: str | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a DRAFT pack + PROPOSED version + pending Approval row.

    Returns ``(approval_id, pack_id, version_id)``.
    """
    factory = get_session_factory()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"approve-{uuid.uuid4().hex[:6]}",
            name="approve-test",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=SkillPackState.DRAFT,
            enabled=False,
        )
        db.add(pack)
        await db.flush([pack])
        version = SkillPackVersion(
            workspace_id=uuid.UUID(ws_id),
            pack_id=pack.id,
            version_no=1,
            content_hash="x" * 64,
            content_md="## v1\nbody",
            files_json={},
            state=SkillPackVersionState.PROPOSED,
            created_by="evolver",
            source_run_ids=[],
            validation_results={},
        )
        db.add(version)
        await db.flush([version])
        approval = Approval(
            workspace_id=uuid.UUID(ws_id),
            session_id=None,
            agent_id=None,
            run_id=None,
            tool_name="_skill_propose_create",
            tool_args={
                "kind": "skill_pack_create",
                "version_id": str(version.id),
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "rationale": "test",
            },
            summary=f"Evolver proposes {pack.slug}",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=None,
            expires_at=utcnow_naive() + timedelta(days=14),
            resource_type=ApprovalResourceType.SKILL_PACK_CREATE.value,
            resource_id=pack.id,
        )
        db.add(approval)
        await db.flush([approval])
        aid, pid, vid = approval.id, pack.id, version.id
        await db.commit()
    return aid, pid, vid


async def test_approve_skill_create_runs_dispatch_and_returns_result(async_client):
    headers, ws_id = await _register_admin(async_client)
    approval_id, pack_id, version_id = await _seed_skill_create_approval(ws_id=ws_id)

    r = await async_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        headers=headers,
        json={"action": "approve", "reason": "looks good"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval"]["status"] == "approved"
    dr = body["dispatch_result"]
    assert dr is not None
    assert dr["resource_type"] == "skill_pack_create"
    assert dr["audit_action"] == "evolver.applied_skill_pack_create"
    assert dr["applied_object_id"] == str(version_id)

    factory = get_session_factory()
    async with factory() as db:
        pack = await db.get(SkillPack, pack_id)
        assert pack.state == SkillPackState.ACTIVE
        assert pack.enabled is True
        version = await db.get(SkillPackVersion, version_id)
        assert version.state == SkillPackVersionState.ACTIVE


async def test_approve_flow_create_lands_disabled_flow(async_client):
    headers, ws_id = await _register_admin(async_client)
    # Need an in-workspace agent to satisfy the FK.
    factory = get_session_factory()
    async with factory() as db:
        from app.db.models.agent import Agent

        agent = Agent(
            workspace_id=uuid.UUID(ws_id),
            name="cron target",
            description="t",
            persona_md="x",
        )
        db.add(agent)
        await db.flush([agent])
        agent_id = agent.id
        approval = Approval(
            workspace_id=uuid.UUID(ws_id),
            session_id=None,
            agent_id=agent.id,
            run_id=None,
            tool_name="_propose_cronjob_create",
            tool_args={
                "name": "morning okr",
                "schedule": "0 9 * * *",
                "schedule_kind": "cron",
                "schedule_meta": {"expr": "0 9 * * *", "tz": "UTC"},
                "prompt_template": "Tell me my OKR.",
                "target_agent_id": str(agent.id),
                "delivery_channel_ids": [],
                "rationale": "morning briefing",
            },
            summary="cronjob proposal",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=None,
            expires_at=utcnow_naive() + timedelta(days=7),
            resource_type=ApprovalResourceType.FLOW_CREATE.value,
            resource_id=None,
        )
        db.add(approval)
        await db.flush([approval])
        aid = approval.id
        await db.commit()

    r = await async_client.post(
        f"/api/v1/approvals/{aid}/decision",
        headers=headers,
        json={"action": "approve", "reason": "ok"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dispatch_result"]["resource_type"] == "flow_create"
    flow_id = body["dispatch_result"]["applied_object_id"]
    assert flow_id is not None

    factory = get_session_factory()
    async with factory() as db:
        flow = await db.get(Flow, uuid.UUID(flow_id))
        assert flow is not None
        assert flow.workspace_id == uuid.UUID(ws_id)
        assert flow.enabled is False
        assert flow.agent_id == agent_id


async def test_approve_invalid_body_409_and_row_stays_pending(async_client):
    headers, ws_id = await _register_admin(async_client)
    factory = get_session_factory()
    async with factory() as db:
        approval = Approval(
            workspace_id=uuid.UUID(ws_id),
            session_id=None,
            agent_id=None,
            run_id=None,
            tool_name="_skill_propose_create",
            tool_args={},  # missing version_id intentionally
            summary="bad body",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=None,
            expires_at=utcnow_naive() + timedelta(days=14),
            resource_type=ApprovalResourceType.SKILL_PACK_CREATE.value,
            resource_id=None,
        )
        db.add(approval)
        await db.flush([approval])
        aid = approval.id
        await db.commit()

    r = await async_client.post(
        f"/api/v1/approvals/{aid}/decision",
        headers=headers,
        json={"action": "approve", "reason": "test"},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "approval.dispatch_invalid_body"

    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(Approval, aid)
        assert row.status == ApprovalStatus.PENDING, "approve must not commit when dispatch fails"
