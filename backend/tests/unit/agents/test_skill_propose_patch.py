"""Unit tests for ``run_propose_skill_patch`` (M2.1).

Validates the patch happy path, the conflict branch when ``old_text``
is missing, and the dedup branch when the patched body matches an
existing version. Each happy path leaves a candidate
:class:`SkillPackVersion` in PROPOSED state — never ACTIVE — and
files an Approval row tagged ``skill_pack_patch``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.skill_propose import (
    ProposeSkillPatchArgs,
    run_propose_skill_patch,
)
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.models.skills import SkillPackState
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import skill_version as version_svc

pytestmark = pytest.mark.asyncio


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


async def _make_active_pack(db_session, *, workspace, identity, body: str):
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug=f"pack-{uuid.uuid4().hex[:6]}",
        name="Pack",
        description=None,
        version="1.0.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=identity.id,
        state=SkillPackState.ACTIVE,
    )
    await db_session.flush()
    version = await version_svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=body,
        files={},
        created_by="user",
        creator_identity_id=identity.id,
    )
    await version_svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=version.id,
        actor_identity_id=identity.id,
    )
    return pack


async def _enable_evolver(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


async def test_propose_patch_happy_path(db_session, workspace, identity, monkeypatch):
    await _enable_evolver(db_session, workspace)
    pack = await _make_active_pack(
        db_session,
        workspace=workspace,
        identity=identity,
        body="Use git commit -m 'msg'.",
    )
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_patch(
        ProposeSkillPatchArgs(
            pack_id=pack.id,
            old_text="git commit -m 'msg'",
            new_text="git commit --amend",
            rationale="Cleaner workflow on the demo runs",
            supporting_run_ids=["00000000-0000-0000-0000-000000000999"],
        )
    )

    assert result["status"] == "proposed"
    assert result["kind"] == ApprovalResourceType.SKILL_PACK_PATCH.value
    assert result["pack_id"] == str(pack.id)
    assert result["version_no"] == 2

    version = await db_session.get(SkillPackVersion, uuid.UUID(result["version_id"]))
    assert version is not None
    # Critical: the candidate version is PROPOSED, never ACTIVE.
    assert version.state == SkillPackVersionState.PROPOSED
    assert "git commit --amend" in version.content_md
    assert "git commit -m 'msg'" not in version.content_md
    assert version.created_by == "evolver"
    assert version.source_run_ids == ["00000000-0000-0000-0000-000000000999"]

    # ACTIVE version must still be v1.
    repo = SkillPackVersionRepository(db_session)
    active = await repo.get_active(workspace_id=workspace.id, pack_id=pack.id)
    assert active is not None
    assert active.version_no == 1

    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_PATCH.value
    assert approval.resource_id == pack.id
    body = dict(approval.tool_args)
    assert body["old_excerpt_hash"] != body["new_excerpt_hash"]
    assert body["rationale"] == "Cleaner workflow on the demo runs"


async def test_propose_patch_conflict_when_old_text_missing(
    db_session, workspace, identity, monkeypatch
):
    await _enable_evolver(db_session, workspace)
    pack = await _make_active_pack(
        db_session,
        workspace=workspace,
        identity=identity,
        body="Use git commit -m 'msg'.",
    )
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_patch(
        ProposeSkillPatchArgs(
            pack_id=pack.id,
            old_text="git push --force-with-lease",
            new_text="git push",
            rationale="r",
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "evolver.patch_conflict"
    assert "current_excerpt" in result

    rows = list(
        (
            await db_session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace.id,
                    Approval.resource_type == ApprovalResourceType.SKILL_PACK_PATCH.value,
                )
            )
        ).scalars()
    )
    assert rows == []


async def test_propose_patch_dedup_when_result_unchanged(
    db_session, workspace, identity, monkeypatch
):
    await _enable_evolver(db_session, workspace)
    pack = await _make_active_pack(
        db_session,
        workspace=workspace,
        identity=identity,
        body="Hello world.",
    )
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    # No-op patch: replace 'Hello' with 'Hello'. Net body is unchanged →
    # the version repo's content_hash dedup raises and the verb returns
    # ``evolver.duplicate_content_hash``.
    result = await run_propose_skill_patch(
        ProposeSkillPatchArgs(
            pack_id=pack.id,
            old_text="Hello",
            new_text="Hello",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.duplicate_content_hash"


async def test_propose_patch_unknown_pack_rejects(db_session, workspace, identity, monkeypatch):
    await _enable_evolver(db_session, workspace)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_patch(
        ProposeSkillPatchArgs(
            pack_id=uuid.uuid4(),
            old_text="x",
            new_text="y",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.pack_not_found"
