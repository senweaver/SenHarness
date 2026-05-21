"""Unit: ``skill_curator.propose_archive`` (M1.4).

Asserts the Approval row + audit row land with the right shape +
metadata + 7-day TTL, and that a duplicate proposal is a no-op.
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
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_curator as svc

pytestmark = pytest.mark.asyncio


async def _make_stale_pack(db, *, workspace_id):
    now = utcnow_naive()
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Stale pack",
        description=None,
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=SkillPackState.STALE,
    )
    pack.last_used_at = now - timedelta(days=120)
    pack.state_changed_at = now - timedelta(days=100)
    await db.flush([pack])
    return pack


async def test_propose_archive_creates_approval_with_correct_payload(
    db_session, workspace
):
    pack = await _make_stale_pack(db_session, workspace_id=workspace.id)
    started_at = utcnow_naive()

    approval = await svc.propose_archive(
        db_session,
        workspace_id=workspace.id,
        pack=pack,
        reason="curator: stale for >= 90 days",
        use_count_30d=3,
        now=started_at,
    )

    assert approval is not None
    assert approval.workspace_id == workspace.id
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resource_type == ApprovalResourceType.SKILL_PACK_ARCHIVE.value
    assert approval.resource_id == pack.id
    assert approval.tool_name == svc.CURATOR_NON_TOOL_NAME
    assert approval.session_id is None
    assert approval.run_id is None
    assert approval.agent_id is None

    # Body shape
    body = dict(approval.tool_args)
    assert body["kind"] == "skill_pack_archive"
    assert body["pack_id"] == str(pack.id)
    assert body["slug"] == pack.slug
    assert body["reason"] == "curator: stale for >= 90 days"
    assert body["use_count_30d"] == 3
    assert body["last_used_at"] is not None
    assert body["stale_since"] is not None

    # 7-day TTL
    assert approval.expires_at is not None
    expected_ttl = timedelta(days=svc.ARCHIVE_PROPOSAL_TTL_DAYS)
    delta = approval.expires_at - started_at
    # Allow a few seconds slop for `utcnow_naive()` granularity.
    assert abs(delta - expected_ttl) < timedelta(seconds=2)


async def test_propose_archive_writes_audit_row(db_session, workspace):
    pack = await _make_stale_pack(db_session, workspace_id=workspace.id)
    approval = await svc.propose_archive(
        db_session,
        workspace_id=workspace.id,
        pack=pack,
        reason="curator: stale",
        use_count_30d=0,
    )
    assert approval is not None

    audits = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == svc.CURATOR_ARCHIVE_PROPOSED,
                AuditEvent.resource_type == "skill_pack",
                AuditEvent.resource_id == pack.id,
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.workspace_id == workspace.id
    meta = audit.metadata_json or {}
    assert meta["approval_id"] == str(approval.id)
    assert meta["ttl_days"] == svc.ARCHIVE_PROPOSAL_TTL_DAYS
    assert meta["pack_id"] == str(pack.id)


async def test_propose_archive_is_idempotent_on_duplicate(db_session, workspace):
    pack = await _make_stale_pack(db_session, workspace_id=workspace.id)
    first = await svc.propose_archive(
        db_session,
        workspace_id=workspace.id,
        pack=pack,
        reason="initial",
    )
    assert first is not None

    second = await svc.propose_archive(
        db_session,
        workspace_id=workspace.id,
        pack=pack,
        reason="should be deduped",
    )
    assert second is None

    rows = (
        await db_session.execute(
            select(Approval).where(
                Approval.resource_type
                == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                Approval.resource_id == pack.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
