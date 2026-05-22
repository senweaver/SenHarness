"""Integration: ``curator_tick`` end-to-end (M1.4).

Seeds four packs in one workspace + a control pack in a second
workspace and asserts the daily Curator sweep:

* moves an idle ACTIVE → STALE,
* files an Approval row for an over-aged STALE pack,
* leaves an unmistakably-fresh STALE pack alone,
* honours pinning across both stale + archive steps,
* writes ``curator.swept`` audit per workspace.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.audit import AuditEvent
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.db.session import get_session_factory
from app.jobs.curator import curator_tick
from app.services import skill_curator as svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[str, str]:
    email = f"cur-{uuid.uuid4().hex[:8]}@example.com"
    password = "curator-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Curator Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return workspace["id"], body["identity_id"]


async def _seed_pack(
    *,
    ws_id: str,
    state: SkillPackState,
    last_used_days_ago: int | None,
    state_changed_days_ago: int,
    pinned: bool = False,
    slug_prefix: str = "sk",
) -> uuid.UUID:
    factory = get_session_factory()
    now = utcnow_naive()
    async with factory() as db:
        pack = SkillPack(
            workspace_id=uuid.UUID(ws_id),
            slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
            name="Curator Test Pack",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            source=SkillPackSource.WORKSPACE,
            state=state,
        )
        pack.pinned = pinned
        pack.last_used_at = (
            None if last_used_days_ago is None else now - timedelta(days=last_used_days_ago)
        )
        pack.state_changed_at = now - timedelta(days=state_changed_days_ago)
        db.add(pack)
        await db.flush([pack])
        pid = pack.id
        await db.commit()
    return pid


async def test_curator_tick_transitions_and_proposes(async_client):
    ws_id, _ident = await _bootstrap(async_client)

    # 1. ACTIVE pack idle 60d → should flip to STALE.
    pid_stale_target = await _seed_pack(
        ws_id=ws_id,
        state=SkillPackState.ACTIVE,
        last_used_days_ago=60,
        state_changed_days_ago=70,
    )
    # 2. STALE pack older than 90d → should get an archive proposal.
    pid_archive_target = await _seed_pack(
        ws_id=ws_id,
        state=SkillPackState.STALE,
        last_used_days_ago=120,
        state_changed_days_ago=120,
    )
    # 3. ACTIVE pinned pack idle 60d → must NOT transition.
    pid_pinned_active = await _seed_pack(
        ws_id=ws_id,
        state=SkillPackState.ACTIVE,
        last_used_days_ago=60,
        state_changed_days_ago=70,
        pinned=True,
    )
    # 4. STALE pack only 10 days old → must NOT propose.
    pid_young_stale = await _seed_pack(
        ws_id=ws_id,
        state=SkillPackState.STALE,
        last_used_days_ago=20,
        state_changed_days_ago=10,
    )

    summary = await curator_tick({})
    assert summary["status"] == "ok"
    assert summary["workspaces_seen"] >= 1
    assert summary["stale_transitioned"] >= 1
    assert summary["archive_proposed"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        stale_now = (
            await db.execute(select(SkillPack).where(SkillPack.id == pid_stale_target))
        ).scalar_one()
        assert stale_now.state == SkillPackState.STALE

        archived_target = (
            await db.execute(select(SkillPack).where(SkillPack.id == pid_archive_target))
        ).scalar_one()
        assert (
            archived_target.state == SkillPackState.STALE
        )  # transition is the proposal, not the apply

        approvals = (
            (
                await db.execute(
                    select(Approval).where(
                        Approval.workspace_id == uuid.UUID(ws_id),
                        Approval.resource_type == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                        Approval.resource_id == pid_archive_target,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(approvals) == 1
        assert approvals[0].status == ApprovalStatus.PENDING
        assert approvals[0].expires_at is not None

        pinned_now = (
            await db.execute(select(SkillPack).where(SkillPack.id == pid_pinned_active))
        ).scalar_one()
        assert pinned_now.state == SkillPackState.ACTIVE
        assert pinned_now.pinned is True

        young_now = (
            await db.execute(select(SkillPack).where(SkillPack.id == pid_young_stale))
        ).scalar_one()
        assert young_now.state == SkillPackState.STALE
        # No archive proposal for the young STALE pack.
        no_proposal = (
            (
                await db.execute(
                    select(Approval).where(
                        Approval.workspace_id == uuid.UUID(ws_id),
                        Approval.resource_type == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
                        Approval.resource_id == pid_young_stale,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert no_proposal == []

        swept_audits = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                        AuditEvent.action == svc.CURATOR_SWEPT,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(swept_audits) >= 1


async def test_curator_tick_disabled_workspace_short_circuits(async_client):
    ws_id, _ident = await _bootstrap(async_client)

    pid = await _seed_pack(
        ws_id=ws_id,
        state=SkillPackState.ACTIVE,
        last_used_days_ago=60,
        state_changed_days_ago=70,
    )

    # Disable Curator on this workspace.
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE workspaces "
                "SET home_config_json = jsonb_set("
                "  COALESCE(home_config_json, '{}'::jsonb), "
                "  '{curator}', "
                "  '{\"enabled\": false}'::jsonb"
                ") "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(ws_id)},
        )
        await db.commit()

    summary = await curator_tick({})
    assert summary["status"] == "ok"
    assert summary["workspaces_disabled"] >= 1

    async with factory() as db:
        pack = (await db.execute(select(SkillPack).where(SkillPack.id == pid))).scalar_one()
        assert pack.state == SkillPackState.ACTIVE  # unchanged
