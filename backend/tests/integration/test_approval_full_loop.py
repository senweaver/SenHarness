"""End-to-end: evolver propose → admin approve → SkillPack ACTIVE (M2.5).

The full chain spans M2.1 (propose verb), M1.4 widened approvals, and
the new M2.5 dispatch handler. Asserts the audit chain a real workspace
will see in production:

* ``evolver.proposed_skill_create`` — written by the propose verb
* ``approval.decide`` — written by the API decision handler
* ``evolver.applied_skill_pack_create`` — written by the dispatch
  handler before the status flip commits

Plus the post-approve invariants: ``SkillPackVersion.state == ACTIVE``,
``SkillPack.state == ACTIVE``, ``SkillPack.enabled == True``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.skill_propose import (
    ProposeSkillCreateArgs,
    run_propose_skill_create,
)
from app.db.models.approval import (
    Approval,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.skill_pack_version import (
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.skills import SkillPack, SkillPackState
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def test_evolver_propose_then_admin_approve_full_loop(
    async_client, db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()

    set_context(
        ToolRunContext(
            run_id=uuid.uuid4(),
            workspace_id=workspace.id,
            session_id=uuid.uuid4(),
            identity_id=identity.id,
            agent_id=uuid.uuid4(),
            scratch_base=Path("/tmp"),
        )
    )
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    # 1) Evolver proposes a new pack.
    await db_session.commit()  # ensure workspace seed visible to evolver session
    proposal = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug=f"loop-{uuid.uuid4().hex[:6]}",
            content_md="## Loop pack\nDo loop things.",
            rationale="Repeated requests in artifacts",
            supporting_run_ids=[],
        )
    )
    assert proposal["status"] == "proposed", proposal
    approval_id = uuid.UUID(proposal["approval_id"])
    pack_id = uuid.UUID(proposal["pack_id"])
    version_id = uuid.UUID(proposal["version_id"])
    await db_session.commit()

    # 2) Login as a workspace admin (the personal workspace owner).
    #    The fixtures' ``identity`` already owns ``workspace``.
    factory = get_session_factory()
    async with factory() as session:
        # Promote the identity by ensuring its membership is OWNER —
        # the conftest workspace fixture already does this on create.
        approval = await session.get(Approval, approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING

    # 3) Build an admin auth header.
    from app.core.security import create_access_token

    token, _exp, _jti = create_access_token(
        identity_id=str(identity.id),
        workspace_id=str(workspace.id),
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": str(workspace.id),
    }

    # 4) Approve via the REST endpoint.
    r = await async_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        headers=headers,
        json={"action": "approve", "reason": "ship it"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval"]["status"] == "approved"
    assert body["dispatch_result"]["resource_type"] == "skill_pack_create"
    assert body["dispatch_result"]["applied_object_id"] == str(version_id)

    # 5) Post-approve invariants.
    async with factory() as session:
        version = await session.get(SkillPackVersion, version_id)
        assert version.state == SkillPackVersionState.ACTIVE
        pack = await session.get(SkillPack, pack_id)
        assert pack.state == SkillPackState.ACTIVE
        assert pack.enabled is True

        # 6) Audit chain — three rows must land.
        actions = (
            (
                await session.execute(
                    select(AuditEvent.action).where(
                        AuditEvent.workspace_id == workspace.id,
                        AuditEvent.action.in_(
                            [
                                "evolver.proposed_skill_create",
                                "approval.decide",
                                "evolver.applied_skill_pack_create",
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "evolver.proposed_skill_create" in actions
        assert "approval.decide" in actions
        assert "evolver.applied_skill_pack_create" in actions
