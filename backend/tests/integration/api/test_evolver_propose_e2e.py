"""Integration coverage for the M2.7 propose-verb tool registry path.

The end-to-end behaviour we care about is "evolver-only" gating: the
runner registers each ``propose_skill_*`` verb only when the calling
agent's policy carries ``agent_kind=evolver``. Other agents see the
verb skipped silently.

The test mimics the runner's tool-registration loop instead of
spinning up a full pydantic-ai model run (which would require a live
LLM provider). This isolates the gating logic + verifies the verb's
runtime semantics (Approval row + version + audit) end-to-end against
a real Postgres engine via the ``db_session`` fixture.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools import BUILTIN_TOOL_REGISTRY
from app.agents.tools._context import ToolRunContext, set_context
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.models.skills import SkillPack, SkillPackState

pytestmark = pytest.mark.asyncio


# ─── Helpers ─────────────────────────────────────────────────
def _set_ctx(workspace, identity):
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


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _resolve_for_kind(toolbox: list[str], *, agent_kind: str | None) -> list[str]:
    """Mirror the runner-side gating logic.

    The runner skips ``available_for_kinds``-restricted tools when the
    agent's kind is not in the allow-list. We replay that filter so
    the test asserts the tool registry's contract directly without
    booting pydantic-ai.
    """
    out: list[str] = []
    for name in toolbox:
        tool = BUILTIN_TOOL_REGISTRY.get(name)
        if tool is None:
            continue
        if tool.available_for_kinds is not None and agent_kind not in tool.available_for_kinds:
            continue
        out.append(name)
    return out


# ─── Tests ───────────────────────────────────────────────────
async def test_propose_verbs_visible_only_to_evolver_agent_kind():
    propose = [
        "propose_skill_create",
        "propose_skill_patch",
        "propose_skill_edit",
        "propose_skill_delete",
        "propose_skill_write_file",
        "propose_skill_remove_file",
    ]

    visible_for_evolver = _resolve_for_kind(propose, agent_kind="evolver")
    assert visible_for_evolver == propose

    visible_for_default = _resolve_for_kind(propose, agent_kind=None)
    assert visible_for_default == []

    visible_for_unknown = _resolve_for_kind(propose, agent_kind="other")
    assert visible_for_unknown == []


async def test_evolver_propose_create_e2e_files_approval_and_audit(
    db_session, workspace, identity, monkeypatch
):
    from app.agents.tools.skill_propose import (
        ProposeSkillCreateArgs,
        run_propose_skill_create,
    )

    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="meeting-notes",
            content_md="## Meeting notes\n\n- one\n- two",
            rationale="distilled from the last 4 standups",
            supporting_run_ids=[
                "00000000-0000-0000-0000-0000000000aa",
                "00000000-0000-0000-0000-0000000000bb",
            ],
        )
    )
    assert result["status"] == "proposed"

    pack = await db_session.get(SkillPack, uuid.UUID(result["pack_id"]))
    assert pack is not None
    assert pack.state == SkillPackState.DRAFT

    version = await db_session.get(SkillPackVersion, uuid.UUID(result["version_id"]))
    assert version is not None
    assert version.state == SkillPackVersionState.PROPOSED

    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_CREATE.value

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "evolver.proposed_skill_create",
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["pack_id"] == str(pack.id)
    assert audits[0].metadata_json["supporting_run_ids"] == [
        "00000000-0000-0000-0000-0000000000aa",
        "00000000-0000-0000-0000-0000000000bb",
    ]
