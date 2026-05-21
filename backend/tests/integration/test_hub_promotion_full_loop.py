"""Integration: M3.3 promote → approve → apply full loop.

End-to-end:

1. Workspace admin POSTs ``/skills/packs/{id}/promote-to-hub`` →
   pending :class:`Approval(resource_type='hub_promotion')`.
2. Same admin POSTs ``/approvals/{id}/decision`` with approve →
   :func:`hub_pull_push.apply_promotion` lands the
   :class:`HubSkillPack` + :class:`HubSkillPackVersion`,
   back-subscribes the source workspace.
3. The audit chain contains
   ``hub.promotion_proposed`` →
   ``hub.promotion_applied`` for the same approval id.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.approval import ApprovalStatus
from app.db.models.audit import AuditEvent
from app.db.session import get_session_factory
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"loop-{uuid.uuid4().hex[:8]}@example.com"
    password = "promotion-loop-pw-very-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Loop", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tokens = body.get("auto_login_tokens") or {}
    token = tokens.get("access_token")
    if not token:
        r = await async_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        token = r.json()["access_token"]
    workspace = body.get("workspace") or {}
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": workspace["id"],
    }
    return headers, workspace["id"], str(body["identity_id"])


async def _seed_skill_pack(*, ws_id: str) -> str:
    factory = get_session_factory()
    from app.db.models.skills import SkillPackSource, SkillPackState
    from app.repositories.skills import SkillPackRepository
    from app.services import skill_version as skill_version_svc

    async with factory() as db:
        pack = await SkillPackRepository(db).create(
            workspace_id=uuid.UUID(ws_id),
            slug=f"loop-{uuid.uuid4().hex[:6]}",
            name="Loop pack",
            description=None,
            version="0.1.0",
            manifest_json={},
            enabled=True,
            metadata_json={},
            created_by=None,
            state=SkillPackState.ACTIVE,
            source=SkillPackSource.WORKSPACE,
        )
        await db.flush([pack])
        await skill_version_svc.create_version(
            db,
            workspace_id=uuid.UUID(ws_id),
            pack_id=pack.id,
            content_md="# loop body — clean, sanitizable",
            files=None,
            created_by="user",
            creator_identity_id=None,
            source_run_ids=[str(uuid.uuid4())],
        )
        await db.commit()
        return str(pack.id)


async def test_promote_then_approve_lands_hub_version(async_client):
    headers, ws_id, identity_id = await _bootstrap(async_client)
    pack_id = await _seed_skill_pack(ws_id=ws_id)

    # ── Step 1: propose ────────────────────────────────────
    r = await async_client.post(
        f"/api/v1/skills/packs/{pack_id}/promote-to-hub",
        headers=headers,
        json={"target_scope": "tenant"},
    )
    assert r.status_code == 202, r.text
    propose_body = r.json()
    approval_id = propose_body["approval_id"]

    # ── Step 2: approve via the standard approvals endpoint ─
    r = await async_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        headers=headers,
        json={"action": "approve", "reason": "looks good"},
    )
    assert r.status_code == 200, r.text

    # ── Step 3: hub state lands ─────────────────────────────
    factory = get_session_factory()
    async with factory() as db:
        # Approval row flipped to APPROVED.
        from app.db.models.approval import Approval

        approval = (
            await db.execute(
                select(Approval).where(Approval.id == uuid.UUID(approval_id))
            )
        ).scalar_one()
        assert approval.status == ApprovalStatus.APPROVED

        # Hub pack + active version exist.
        from app.db.models.hub_skill_pack import HubScope

        pack_repo = HubSkillPackRepository(db)
        version_repo = HubSkillPackVersionRepository(db)
        hub_pack = await pack_repo.get_by_slug(
            scope=HubScope(approval.tool_args["target_scope"]),
            tenant_id=(
                uuid.UUID(approval.tool_args["target_tenant_id"])
                if approval.tool_args.get("target_tenant_id")
                else None
            ),
            slug=approval.tool_args["target_slug"],
        )
        assert hub_pack is not None
        active = await version_repo.get_active(hub_pack_id=hub_pack.id)
        assert active is not None
        assert active.is_active is True
        assert active.version_no == 1
        assert active.content_md  # body landed

        # Source workspace got back-subscribed.
        sub = await WorkspaceHubSubscriptionRepository(db).get_by_pack(
            workspace_id=uuid.UUID(ws_id), hub_pack_id=hub_pack.id
        )
        assert sub is not None
        assert sub.auto_pull is True
        assert sub.last_pulled_version_no == 1

        # Audit chain: proposed + applied for the same approval id.
        actions = (
            (
                await db.execute(
                    select(AuditEvent.action, AuditEvent.metadata_json).where(
                        AuditEvent.workspace_id == uuid.UUID(ws_id),
                    )
                )
            )
            .all()
        )
        labelled = [(a, (m or {}).get("approval_id")) for (a, m) in actions]
        assert ("hub.promotion_proposed", str(approval.id)) in labelled
        assert ("hub.promotion_applied", str(approval.id)) in labelled
