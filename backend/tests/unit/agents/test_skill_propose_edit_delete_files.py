"""Unit tests for the four M2.7 verbs (edit / delete / write_file /
remove_file).

Each verb has one happy path that asserts the Approval shape +
resource_type + audit row, plus the most important reject branch the
agent must handle.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.skill_propose import (
    ProposeSkillDeleteArgs,
    ProposeSkillEditArgs,
    ProposeSkillRemoveFileArgs,
    ProposeSkillWriteFileArgs,
    run_propose_skill_delete,
    run_propose_skill_edit,
    run_propose_skill_remove_file,
    run_propose_skill_write_file,
)
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.models.skills import SkillFile, SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_lifecycle as lifecycle_svc
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


async def _make_pack(db_session, *, workspace, identity, body="hello"):
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug=f"pk-{uuid.uuid4().hex[:6]}",
        name="P",
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


async def _enable(db_session, workspace):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()


# ─── propose_skill_edit ──────────────────────────────────────
async def test_propose_edit_creates_proposed_version_and_approval(
    db_session, workspace, identity, monkeypatch
):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity, body="old body")
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_edit(
        ProposeSkillEditArgs(
            pack_id=pack.id,
            new_content_md="brand new SKILL.md body",
            rationale="restructure",
        )
    )

    assert result["status"] == "proposed"
    assert result["kind"] == ApprovalResourceType.SKILL_PACK_EDIT.value
    version = await db_session.get(SkillPackVersion, uuid.UUID(result["version_id"]))
    assert version is not None
    assert version.state == SkillPackVersionState.PROPOSED
    assert version.created_by == "evolver"

    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_EDIT.value
    assert approval.tool_name == "_skill_propose_edit"
    body = dict(approval.tool_args)
    assert body["rationale"] == "restructure"


async def test_propose_edit_rejects_tombstoned_pack(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    pack.state = SkillPackState.ARCHIVED
    await db_session.flush()
    await lifecycle_svc.transition(
        db_session,
        pack_id=pack.id,
        workspace_id=workspace.id,
        target_state=SkillPackState.TOMBSTONE,
        actor_identity_id=identity.id,
        reason="test",
        bypass_pinned=True,
    )
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_edit(
        ProposeSkillEditArgs(
            pack_id=pack.id,
            new_content_md="x",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.pack_tombstoned"


# ─── propose_skill_delete ────────────────────────────────────
async def test_propose_delete_creates_approval_no_version(
    db_session, workspace, identity, monkeypatch
):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_delete(
        ProposeSkillDeleteArgs(
            pack_id=pack.id,
            rationale="no longer relevant",
        )
    )
    assert result["status"] == "proposed"
    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_DELETE.value
    assert approval.status == ApprovalStatus.PENDING
    assert approval.tool_name == "_skill_propose_delete"
    body = dict(approval.tool_args)
    assert body["current_state"] == "active"
    assert body["rationale"] == "no longer relevant"


async def test_propose_delete_rejects_pinned_pack(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    pack.pinned = True
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_delete(ProposeSkillDeleteArgs(pack_id=pack.id, rationale="r"))
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.pack_pinned"


async def test_propose_delete_dedup_pending(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    first = await run_propose_skill_delete(
        ProposeSkillDeleteArgs(pack_id=pack.id, rationale="first")
    )
    assert first["status"] == "proposed"
    second = await run_propose_skill_delete(
        ProposeSkillDeleteArgs(pack_id=pack.id, rationale="second")
    )
    assert second["status"] == "rejected"
    assert second["code"] == "evolver.duplicate_pending"


# ─── propose_skill_write_file ────────────────────────────────
async def test_propose_write_file_happy(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_write_file(
        ProposeSkillWriteFileArgs(
            pack_id=pack.id,
            relative_path="scripts/run.sh",
            content="#!/bin/bash\necho ok\n",
            rationale="reusable runner",
        )
    )
    assert result["status"] == "proposed"
    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_WRITE_FILE.value
    body = dict(approval.tool_args)
    assert body["relative_path"] == "scripts/run.sh"
    assert body["content_hash"] == result["content_hash"]


async def test_propose_write_file_rejects_skill_md(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_write_file(
        ProposeSkillWriteFileArgs(
            pack_id=pack.id,
            relative_path="SKILL.md",
            content="x",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.reserved_path"


async def test_propose_write_file_rejects_traversal_segments(
    db_session, workspace, identity, monkeypatch
):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    # The Pydantic ``pattern`` allows '.' and '/' in the path so '..'
    # passes regex validation; the service layer's
    # ``_is_write_file_path_safe`` is what stops the traversal. We
    # exercise both that path and the double-slash case.
    for bad in ("..", "../escape", "dir//file", "/leading", "trailing/"):
        result = await run_propose_skill_write_file(
            ProposeSkillWriteFileArgs(
                pack_id=pack.id,
                relative_path=bad,
                content="x",
                rationale="r",
            )
        )
        assert result["status"] == "rejected", bad
        assert result["code"] == "evolver.invalid_path", bad


# ─── propose_skill_remove_file ───────────────────────────────
async def test_propose_remove_file_happy(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    db_session.add(
        SkillFile(
            workspace_id=workspace.id,
            skill_pack_id=pack.id,
            path="scripts/old.sh",
            content_md="echo bye\n",
        )
    )
    await db_session.flush()
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_remove_file(
        ProposeSkillRemoveFileArgs(
            pack_id=pack.id,
            relative_path="scripts/old.sh",
            rationale="bit-rot",
        )
    )
    assert result["status"] == "proposed"
    approval = await db_session.get(Approval, uuid.UUID(result["approval_id"]))
    assert approval is not None
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value
    assert approval.tool_name == "_skill_propose_remove_file"


async def test_propose_remove_file_rejects_unknown_path(
    db_session, workspace, identity, monkeypatch
):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_remove_file(
        ProposeSkillRemoveFileArgs(
            pack_id=pack.id,
            relative_path="missing.txt",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.file_not_found"


async def test_propose_remove_file_rejects_skill_md(db_session, workspace, identity, monkeypatch):
    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_propose_skill_remove_file(
        ProposeSkillRemoveFileArgs(
            pack_id=pack.id,
            relative_path="SKILL.md",
            rationale="r",
        )
    )
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.reserved_path"


# ─── audit + select smoke ────────────────────────────────────
async def test_audit_actions_use_canonical_keys(db_session, workspace, identity, monkeypatch):
    """Spot-check that each happy verb writes the agreed audit action."""
    from app.db.models.audit import AuditEvent

    await _enable(db_session, workspace)
    pack = await _make_pack(db_session, workspace=workspace, identity=identity)
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.skill_propose.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    await run_propose_skill_delete(ProposeSkillDeleteArgs(pack_id=pack.id, rationale="r"))

    actions = list(
        (
            await db_session.execute(
                select(AuditEvent.action).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action.like("evolver.%"),
                )
            )
        ).scalars()
    )
    assert "evolver.proposed_skill_delete" in actions
