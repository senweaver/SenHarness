"""Integration: ``process_expired_approvals`` end-to-end (M2.5).

Seeds 5 expired pending approvals across the supported resource types
plus one row inside the 24h reminder horizon. Asserts:

* skill_pack_archive → ``status=approved`` + ``approval.expired_auto_executed`` audit
* every other resource type → ``status=expired`` + ``approval.expired_rejected`` audit
* the row in the reminder horizon stays pending but flips ``reminder_sent=True``
  and writes ``approval.expiring_reminder_sent`` audit

The reminder + expiry passes share one tick; both run inside one
hourly cron call.
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
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.db.session import get_session_factory
from app.jobs.approval_ttl import process_expired_approvals

pytestmark = pytest.mark.asyncio


async def _make_workspace(async_client) -> str:
    email = f"ttl-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "TTL Tester",
            "password": "ttl-test-password-very-long",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["workspace"]["id"]


async def _seed_pack(*, ws_id: str, slug: str) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=slug,
            name=slug.title(),
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=SkillPackState.STALE,
        )
        db.add(pack)
        await db.flush([pack])
        pack_id = pack.id
        await db.commit()
    return pack_id


async def _seed_approval(
    *,
    ws_id: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    expires_in_seconds: int,
    body: dict | None = None,
    tool_name: str = "_test",
) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        row = Approval(
            workspace_id=uuid.UUID(ws_id),
            session_id=None,
            agent_id=None,
            run_id=None,
            tool_name=tool_name,
            tool_args=body or {},
            summary=f"ttl-test {resource_type}",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=None,
            expires_at=utcnow_naive() + timedelta(seconds=expires_in_seconds),
            resource_type=resource_type,
            resource_id=resource_id,
        )
        db.add(row)
        await db.flush([row])
        rid = row.id
        await db.commit()
    return rid


async def test_process_expired_approvals_routes_each_resource_type(async_client):
    ws_id = await _make_workspace(async_client)
    pack_id = await _seed_pack(ws_id=ws_id, slug=f"archive-{uuid.uuid4().hex[:6]}")

    # 5 expired (negative TTL) approvals across resource types
    archive_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
        resource_id=pack_id,
        expires_in_seconds=-60,
        body={"slug": "archive-test"},
    )
    create_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_CREATE.value,
        resource_id=None,
        expires_in_seconds=-60,
        body={"version_id": str(uuid.uuid4())},
    )
    delete_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_DELETE.value,
        resource_id=None,
        expires_in_seconds=-60,
    )
    flow_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.FLOW_CREATE.value,
        resource_id=None,
        expires_in_seconds=-60,
    )
    write_file_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_WRITE_FILE.value,
        resource_id=None,
        expires_in_seconds=-60,
    )
    # 1 expiring-soon (12h to expiry) for the reminder pass
    soon_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_PATCH.value,
        resource_id=None,
        expires_in_seconds=12 * 3600,
    )

    summary = await process_expired_approvals({})

    assert summary["status"] == "ok"
    assert summary["expired_seen"] >= 5
    # Exactly 1 archive auto-executed, others (4) rejected.
    assert summary["expired_auto_executed"] >= 1
    assert summary["expired_rejected"] >= 4
    assert summary["expiring_seen"] >= 1
    assert summary["expiring_reminded"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        # Archive — approved + dispatch ran (pack archived).
        archive_row = await db.get(Approval, archive_id)
        assert archive_row.status == ApprovalStatus.APPROVED
        pack = await db.get(SkillPack, pack_id)
        assert pack.state == SkillPackState.ARCHIVED

        for rid in (create_id, delete_id, flow_id, write_file_id):
            row = await db.get(Approval, rid)
            assert row.status == ApprovalStatus.EXPIRED, (
                f"row {rid} expected EXPIRED, got {row.status}"
            )

        # Reminder — still pending but flagged.
        soon_row = await db.get(Approval, soon_id)
        assert soon_row.status == ApprovalStatus.PENDING
        assert soon_row.reminder_sent is True

        # Audit — at least one of each new key landed.
        for action in (
            "approval.expired_auto_executed",
            "approval.expired_rejected",
            "approval.expiring_reminder_sent",
        ):
            audit = (
                await db.execute(select(AuditEvent).where(AuditEvent.action == action).limit(1))
            ).scalar_one_or_none()
            assert audit is not None, f"missing audit row for {action}"


async def test_reminder_pass_does_not_double_send(async_client):
    """A second tick on the same row leaves ``reminder_sent`` True
    and does not write a second ``approval.expiring_reminder_sent``
    audit row.
    """
    ws_id = await _make_workspace(async_client)
    soon_id = await _seed_approval(
        ws_id=ws_id,
        resource_type=ApprovalResourceType.SKILL_PACK_PATCH.value,
        resource_id=None,
        expires_in_seconds=8 * 3600,
    )

    await process_expired_approvals({})
    factory = get_session_factory()
    async with factory() as db:
        first_count = (
            await db.execute(
                select(AuditEvent.id).where(
                    AuditEvent.action == "approval.expiring_reminder_sent",
                    AuditEvent.resource_id == soon_id,
                )
            )
        ).all()

    summary = await process_expired_approvals({})
    assert summary["expiring_reminded"] == 0

    async with factory() as db:
        second_count = (
            await db.execute(
                select(AuditEvent.id).where(
                    AuditEvent.action == "approval.expiring_reminder_sent",
                    AuditEvent.resource_id == soon_id,
                )
            )
        ).all()
    assert len(first_count) == len(second_count) == 1
