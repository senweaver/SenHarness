"""Unit tests for ``run_propose_skill_create`` (M2.1 + M2.7).

Covers the happy path (DRAFT pack + PROPOSED v1 + Approval row +
audit), and every reject branch the agent must handle: disabled
workspace, breaker tripped, slug already in use, slug tombstoned,
internal error → breaker bump.
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
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.models.skills import SkillPack, SkillPackState

pytestmark = pytest.mark.asyncio


def _set_ctx(workspace, identity):
    ctx = ToolRunContext(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        session_id=uuid.uuid4(),
        identity_id=identity.id,
        agent_id=uuid.uuid4(),
        scratch_base=Path("/tmp"),
    )
    set_context(ctx)
    return ctx


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _enable_evolver(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


async def test_propose_create_happy_path(db_session, workspace, identity, monkeypatch):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="rfp-research",
            content_md="## Research RFPs\n\nDo X, then Y.",
            rationale="Recurring user requests",
            supporting_run_ids=["00000000-0000-0000-0000-000000000123"],
        )
    )

    assert result["status"] == "proposed"
    assert result["kind"] == ApprovalResourceType.SKILL_PACK_CREATE.value
    assert result["slug"] == "rfp-research"
    assert "approval_id" in result
    assert "pack_id" in result
    assert "version_id" in result
    assert result["version_no"] == 1

    pack = await db_session.get(SkillPack, uuid.UUID(result["pack_id"]))
    assert pack is not None
    assert pack.workspace_id == workspace.id
    assert pack.slug == "rfp-research"
    assert pack.state == SkillPackState.DRAFT
    assert pack.enabled is False

    version = await db_session.get(SkillPackVersion, uuid.UUID(result["version_id"]))
    assert version is not None
    assert version.state == SkillPackVersionState.PROPOSED
    assert version.created_by == "evolver"
    assert version.creator_identity_id == identity.id

    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.workspace_id == workspace.id
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_CREATE.value
    assert approval.resource_id == pack.id
    assert approval.session_id is None
    assert approval.tool_name == "_skill_propose_create"
    assert approval.expires_at is not None

    body = dict(approval.tool_args)
    assert body["kind"] == ApprovalResourceType.SKILL_PACK_CREATE.value
    assert body["slug"] == "rfp-research"
    assert body["pack_id"] == str(pack.id)
    assert body["version_id"] == str(version.id)
    assert body["rationale"] == "Recurring user requests"
    assert body["supporting_run_ids"] == ["00000000-0000-0000-0000-000000000123"]

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "evolver.proposed_skill_create",
                    AuditEvent.resource_type == "skill_pack",
                    AuditEvent.resource_id == pack.id,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["approval_id"] == str(approval.id)


async def test_propose_create_disabled_workspace_rejects(
    db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": False}}
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="should-not-land",
            content_md="x",
            rationale="x",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.disabled"

    packs = list(
        (
            await db_session.execute(
                select(SkillPack).where(
                    SkillPack.workspace_id == workspace.id,
                    SkillPack.slug == "should-not-land",
                )
            )
        ).scalars()
    )
    assert packs == []
    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "evolver.propose_rejected",
                    AuditEvent.workspace_id == workspace.id,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["code"] == "evolver.disabled"


async def test_propose_create_slug_in_use_rejects(db_session, workspace, identity, monkeypatch):
    from app.repositories.skills import SkillPackRepository

    await _enable_evolver(db_session, workspace)
    await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug="taken",
        name="Existing",
        description=None,
        version="1.0.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=SkillPackState.ACTIVE,
    )
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="taken",
            content_md="x",
            rationale="x",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.slug_in_use"


async def test_propose_create_tombstoned_slug_rejects(db_session, workspace, identity, monkeypatch):
    from app.db.models.tombstone_slug import TombstoneSlug

    await _enable_evolver(db_session, workspace)
    db_session.add(
        TombstoneSlug(
            workspace_id=workspace.id,
            slug="killed",
            original_pack_id=uuid.uuid4(),
            last_content_hash="x" * 64,
        )
    )
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="killed",
            content_md="x",
            rationale="x",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.slug_tombstoned"


async def test_propose_create_breaker_tripped_rejects(db_session, workspace, identity, monkeypatch):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    async def _open(**_kwargs):
        return True

    monkeypatch.setattr("app.agents.tools.skill_propose.is_breaker_open", _open)

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="never-lands",
            content_md="x",
            rationale="x",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.breaker_tripped"

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "evolver.breaker_tripped",
                    AuditEvent.workspace_id == workspace.id,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1


async def test_propose_create_rate_limit_rejects(db_session, workspace, identity, monkeypatch):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    async def _denied(**_kwargs):
        return False

    monkeypatch.setattr("app.agents.tools.skill_propose.consume_rate", _denied)

    result = await run_propose_skill_create(
        ProposeSkillCreateArgs(
            slug="rate-blocked",
            content_md="x",
            rationale="x",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.rate_limited"
