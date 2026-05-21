"""Integration: pinned packs are immune to the Curator (M1.4 + M1.1).

Asserts the M1 acceptance bullet "Pin a stale pack → never auto-archived":
the pinned ACTIVE pack stays ACTIVE through ``curator_tick`` and the
``skill.transition_skipped_pinned`` audit row lands so admins can see
the skip in the activity feed.
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
)
from app.db.models.audit import AuditEvent
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.db.session import get_session_factory
from app.jobs.curator import curator_tick

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> str:
    email = f"pin-{uuid.uuid4().hex[:8]}@example.com"
    password = "curator-pin-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Pin Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return workspace["id"]


async def _seed_pinned_idle_pack(
    *, ws_id: str, days_unused: int
) -> uuid.UUID:
    factory = get_session_factory()
    now = utcnow_naive()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"pin-{uuid.uuid4().hex[:8]}",
            name="Pinned Pack",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=SkillPackState.ACTIVE,
        )
        pack.pinned = True
        pack.last_used_at = now - timedelta(days=days_unused)
        pack.state_changed_at = now - timedelta(days=days_unused + 5)
        db.add(pack)
        await db.flush([pack])
        pid = pack.id
        await db.commit()
    return pid


async def test_pinned_active_pack_is_never_archived(async_client):
    ws_id = await _bootstrap(async_client)
    pinned_pid = await _seed_pinned_idle_pack(ws_id=ws_id, days_unused=60)

    summary = await curator_tick({})
    assert summary["status"] == "ok"
    assert summary["stale_skipped_pinned"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        pack = (
            await db.execute(select(SkillPack).where(SkillPack.id == pinned_pid))
        ).scalar_one()
        assert pack.state == SkillPackState.ACTIVE
        assert pack.pinned is True

        # No archive approval for a pinned pack.
        approvals = (
            await db.execute(
                select(Approval).where(
                    Approval.workspace_id == uuid.UUID(ws_id),
                    Approval.resource_type
                    == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                    Approval.resource_id == pinned_pid,
                )
            )
        ).scalars().all()
        assert approvals == []

        # The skip audit lands so the activity feed surfaces the skip.
        skip_audits = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == uuid.UUID(ws_id),
                    AuditEvent.action == "skill.transition_skipped_pinned",
                    AuditEvent.resource_id == pinned_pid,
                )
            )
        ).scalars().all()
        assert len(skip_audits) >= 1


async def test_pinned_stale_pack_is_never_proposed_for_archive(async_client):
    """Pin survives even when a pack has been STALE longer than archive_after_days."""
    ws_id = await _bootstrap(async_client)
    factory = get_session_factory()
    now = utcnow_naive()

    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"pin-stale-{uuid.uuid4().hex[:8]}",
            name="Pinned + Stale",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=SkillPackState.STALE,
        )
        pack.pinned = True
        pack.last_used_at = now - timedelta(days=120)
        pack.state_changed_at = now - timedelta(days=120)
        db.add(pack)
        await db.flush([pack])
        pid = pack.id
        await db.commit()

    summary = await curator_tick({})
    assert summary["archive_skipped_pinned"] >= 1

    async with factory() as db:
        approvals = (
            await db.execute(
                select(Approval).where(
                    Approval.workspace_id == uuid.UUID(ws_id),
                    Approval.resource_type
                    == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                    Approval.resource_id == pid,
                )
            )
        ).scalars().all()
        assert approvals == []
