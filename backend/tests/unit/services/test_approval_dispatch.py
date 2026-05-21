"""Unit tests for ``app.services.approval_dispatch`` (M2.5).

Each of the seven dispatchable resource types gets a happy-path case
plus one failure case that verifies the dispatch error contract
(raises ``DispatchError`` so the API layer rolls back the surrounding
transaction). Tests use the shared ``db_session`` fixture so they
skip cleanly when Postgres isn't available.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.flow import Flow
from app.db.models.skill_pack_version import (
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.skills import (
    SkillFile,
    SkillPack,
    SkillPackSource,
    SkillPackState,
)
from app.repositories.approval import ApprovalRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services.approval_dispatch import (
    AUDIT_PER_RESOURCE,
    DispatchError,
    DispatchResult,
    dispatch_approved_approval,
)
from app.services.skill_version import create_version

pytestmark = pytest.mark.asyncio


# ─── Helpers ──────────────────────────────────────────────────
async def _make_pack(
    db_session,
    workspace,
    *,
    slug: str,
    state: SkillPackState = SkillPackState.ACTIVE,
    pinned: bool = False,
) -> SkillPack:
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug=slug,
        name=slug.title(),
        description=None,
        version="0.1.0",
        publisher=None,
        signature=None,
        source=SkillPackSource.WORKSPACE,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=state,
        pinned=pinned,
    )
    await db_session.flush([pack])
    return pack


async def _make_version(
    db_session, workspace, pack, *, content_md: str = "## v1\n"
) -> SkillPackVersion:
    version = await create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=content_md,
        files=None,
        created_by="evolver",
        creator_identity_id=None,
    )
    return version


async def _make_pending_approval(
    db_session,
    workspace,
    *,
    resource_type: str,
    resource_id: uuid.UUID | None,
    body: dict,
    tool_name: str = "_dispatch_test",
    summary: str = "test",
) -> Approval:
    repo = ApprovalRepository(db_session)
    row = await repo.create(
        workspace_id=workspace.id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name=tool_name,
        tool_args=body,
        summary=summary,
        requested_by_identity_id=None,
        expires_at=utcnow_naive() + timedelta(days=7),
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return row


# ─── Happy path × 7 resource_types ────────────────────────────
async def test_dispatch_skill_pack_create_activates_version_and_promotes_pack(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="created", state=SkillPackState.DRAFT)
    pack.enabled = False
    await db_session.flush([pack])
    version = await _make_version(db_session, workspace, pack, content_md="## v1\nbody\n")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_CREATE.value,
        resource_id=pack.id,
        body={"version_id": str(version.id), "slug": pack.slug},
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )

    assert isinstance(result, DispatchResult)
    assert result.audit_action == "evolver.applied_skill_pack_create"
    assert result.applied_object_id == version.id

    refreshed_version = await db_session.get(SkillPackVersion, version.id)
    assert refreshed_version is not None
    assert refreshed_version.state == SkillPackVersionState.ACTIVE

    refreshed_pack = await db_session.get(SkillPack, pack.id)
    assert refreshed_pack is not None
    assert refreshed_pack.state == SkillPackState.ACTIVE
    assert refreshed_pack.enabled is True


async def test_dispatch_skill_pack_patch_activates_version_only(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="patched")
    v1 = await _make_version(db_session, workspace, pack, content_md="## v1\n")
    # First version must be active to mirror the patch flow.
    from app.services.skill_version import activate_version  # noqa: PLC0415

    await activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=v1.id,
        actor_identity_id=None,
    )
    v2 = await _make_version(db_session, workspace, pack, content_md="## v2 patched\n")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_PATCH.value,
        resource_id=pack.id,
        body={"version_id": str(v2.id), "pack_id": str(pack.id)},
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result is not None
    assert result.applied_object_id == v2.id

    v1_refresh = await db_session.get(SkillPackVersion, v1.id)
    v2_refresh = await db_session.get(SkillPackVersion, v2.id)
    assert v1_refresh.state == SkillPackVersionState.RETIRED
    assert v2_refresh.state == SkillPackVersionState.ACTIVE


async def test_dispatch_skill_pack_edit_uses_same_handler_as_patch(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="edited")
    version = await _make_version(db_session, workspace, pack, content_md="## edited\n")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_EDIT.value,
        resource_id=pack.id,
        body={"version_id": str(version.id), "pack_id": str(pack.id)},
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "evolver.applied_skill_pack_edit"


async def test_dispatch_skill_pack_delete_archives_pack(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="to-delete")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_DELETE.value,
        resource_id=pack.id,
        body={"pack_id": str(pack.id)},
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "evolver.applied_skill_pack_delete"

    refreshed = await db_session.get(SkillPack, pack.id)
    assert refreshed.state == SkillPackState.ARCHIVED


async def test_dispatch_skill_pack_archive_uses_curator_audit(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="curator-archive")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
        resource_id=pack.id,
        body={"pack_id": str(pack.id), "slug": pack.slug},
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "curator.applied_archive"
    audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "curator.applied_archive",
                AuditEvent.resource_id == pack.id,
            )
        )
    ).scalar_one_or_none()
    assert audit is not None


async def test_dispatch_skill_pack_write_file_creates_or_updates_file(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="file-host")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_WRITE_FILE.value,
        resource_id=pack.id,
        body={
            "pack_id": str(pack.id),
            "relative_path": "scripts/run.sh",
            "content": "#!/bin/sh\necho hi\n",
        },
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "evolver.applied_skill_pack_write_file"
    files = await SkillFileRepository(db_session).list_for_pack(
        workspace_id=workspace.id, skill_pack_id=pack.id
    )
    assert any(f.path == "scripts/run.sh" for f in files)


async def test_dispatch_skill_pack_remove_file_soft_deletes_file(
    db_session, workspace, identity
):
    pack = await _make_pack(db_session, workspace, slug="file-removal")
    file_repo = SkillFileRepository(db_session)
    await file_repo.create(
        workspace_id=workspace.id,
        skill_pack_id=pack.id,
        path="scripts/old.sh",
        content_md="echo old",
    )
    await db_session.flush()

    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value,
        resource_id=pack.id,
        body={"pack_id": str(pack.id), "relative_path": "scripts/old.sh"},
    )
    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "evolver.applied_skill_pack_remove_file"
    remaining = await file_repo.list_for_pack(
        workspace_id=workspace.id, skill_pack_id=pack.id
    )
    assert all(f.path != "scripts/old.sh" for f in remaining)
    # ensure soft-delete row exists (deleted_at not NULL)
    raw = (
        await db_session.execute(
            select(SkillFile).where(SkillFile.skill_pack_id == pack.id)
        )
    ).scalars().all()
    assert any(f.deleted_at is not None and f.path == "scripts/old.sh" for f in raw)


async def test_dispatch_flow_create_lands_disabled_flow(
    db_session, workspace, agent, identity
):
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.FLOW_CREATE.value,
        resource_id=None,
        body={
            "name": "morning okr",
            "schedule": "0 9 * * *",
            "schedule_kind": "cron",
            "schedule_meta": {"expr": "0 9 * * *", "tz": "UTC"},
            "prompt_template": "Read me my OKR.",
            "target_agent_id": str(agent.id),
            "delivery_channel_ids": [],
            "rationale": "morning briefing",
        },
    )

    result = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert result.audit_action == "evolver.applied_flow_create"

    flow = await db_session.get(Flow, result.applied_object_id)
    assert flow is not None
    # Critical second-gate invariant — even after admin approval the
    # Flow lands with enabled=False.
    assert flow.enabled is False
    assert flow.workspace_id == workspace.id


# ─── Failure path × 1 (rollback contract) ────────────────────
async def test_dispatch_invalid_body_raises_dispatch_error(
    db_session, workspace, identity
):
    """Missing version_id in a create proposal must raise DispatchError.

    The API layer relies on the raise to roll back the approve
    transaction so the row stays pending and the admin can retry.
    """
    pack = await _make_pack(db_session, workspace, slug="bad-body")
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=ApprovalResourceType.SKILL_PACK_CREATE.value,
        resource_id=pack.id,
        body={},  # missing version_id intentionally
    )

    with pytest.raises(DispatchError) as excinfo:
        await dispatch_approved_approval(
            db_session, approval=approval, actor_identity_id=identity.id
        )
    assert excinfo.value.code == "approval.dispatch_invalid_body"


async def test_dispatch_legacy_tool_call_returns_none(db_session, workspace, identity):
    """Approvals where ``resource_type is None`` must be no-op."""
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type=None,
        resource_id=None,
        body={"command": "ls"},
        tool_name="execute",
    )
    out = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert out is None


async def test_dispatch_unknown_resource_type_returns_none_no_raise(
    db_session, workspace, identity
):
    """Unknown ``resource_type`` (e.g. M3 hub_promotion) must return
    ``None`` so the approve flow proceeds — only the TTL processor
    rejects on expiry."""
    approval = await _make_pending_approval(
        db_session,
        workspace,
        resource_type="hub_promotion",
        resource_id=None,
        body={},
    )
    out = await dispatch_approved_approval(
        db_session, approval=approval, actor_identity_id=identity.id
    )
    assert out is None


def test_audit_action_mapping_is_complete():
    """Pure-function safety net — every ApprovalResourceType has a
    stable ``evolver.applied_*`` / ``curator.applied_*`` audit key.
    """
    for rt in ApprovalResourceType:
        assert rt.value in AUDIT_PER_RESOURCE, f"missing audit action key for {rt.value}"
