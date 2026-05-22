"""Unit: ``hub_pull_push.initiate_promotion`` (M3.3).

Three rules under test:

* Blockers from the M3.2 preview short-circuit and surface as the
  appropriate typed error (``HubPromotionBlocked`` or the more
  specific ``HubScopePermissionDenied`` / ``HubSlugTombstoned``).
* PLATFORM scope requested by a non-platform-admin is refused with
  the M3.1 ``hub.scope_permission_denied`` code, and **no** approval
  row is created.
* Happy path inserts an :class:`Approval` row with
  ``resource_type='hub_promotion'`` and a 30-day TTL plus the
  ``hub.promotion_proposed`` audit row.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.errors import HubScopePermissionDenied
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.audit import AuditEvent
from app.db.models.hub_skill_pack import HubScope
from app.db.models.identity import PlatformRole
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import hub_promote_pipeline as preview_svc
from app.services import hub_pull_push as hub_pp_svc
from app.services import skill_version as skill_version_svc

pytestmark = pytest.mark.asyncio


async def _make_pack_and_version(db, *, identity, slug_prefix: str = "ws"):
    from app.services import workspace as ws_svc

    workspace = await ws_svc.create_workspace(
        db,
        name="Promo init",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db.flush()
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace.id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Test pack",
        description=None,
        version="0.1.0",
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=identity.id,
        state=SkillPackState.ACTIVE,
    )
    await db.flush()
    version = await skill_version_svc.create_version(
        db,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=("# clean\n\n no PII here at all"),
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
        source_run_ids=[str(uuid.uuid4())],
    )
    await db.flush()
    return workspace, pack, version


async def test_blocker_raises_and_no_approval_row(db_session, identity):
    workspace, pack, version = await _make_pack_and_version(db_session, identity=identity)

    # Force a sanitizer-required failure — should raise + leave no
    # approval row.
    from app.services.skill_sanitize import (
        SanitizationStats,
        SanitizedHubPayload,
    )

    def _failing_sanitize(body, run_ids, **kwargs):
        return SanitizedHubPayload(
            content_md=body,
            source_run_id_hashes=[],
            stats=SanitizationStats(failure_reason="boom"),
        )

    with (
        patch(
            "app.services.hub_promote_pipeline.sanitize_for_hub",
            side_effect=_failing_sanitize,
        ),
        pytest.raises(hub_pp_svc.HubPromotionBlocked) as exc_info,
    ):
        await hub_pp_svc.initiate_promotion(
            db_session,
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_scope=HubScope.TENANT,
            actor=identity,
            version_id=version.id,
        )

    assert preview_svc.BLOCKER_SANITIZER_REQUIRED_FAILED in exc_info.value.extras["blockers"]

    rows = (
        (await db_session.execute(select(Approval).where(Approval.workspace_id == workspace.id)))
        .scalars()
        .all()
    )
    assert all(r.resource_type != hub_pp_svc.HUB_PROMOTION_RESOURCE_TYPE for r in rows)


async def test_platform_scope_requires_platform_admin(db_session, identity):
    workspace, pack, version = await _make_pack_and_version(
        db_session, identity=identity, slug_prefix="plat"
    )
    # ``identity.platform_role`` defaults to USER; preview returns the
    # scope-permission blocker → the wrapper translates to the typed
    # error so the API can render the M3.1 403 envelope.
    with pytest.raises(HubScopePermissionDenied) as exc_info:
        await hub_pp_svc.initiate_promotion(
            db_session,
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_scope=HubScope.PLATFORM,
            actor=identity,
            version_id=version.id,
        )
    assert exc_info.value.code == "hub.scope_permission_denied"

    rows = (
        (await db_session.execute(select(Approval).where(Approval.workspace_id == workspace.id)))
        .scalars()
        .all()
    )
    assert all(r.resource_type != hub_pp_svc.HUB_PROMOTION_RESOURCE_TYPE for r in rows)


async def test_happy_creates_approval_and_audit(db_session, identity):
    workspace, pack, version = await _make_pack_and_version(
        db_session, identity=identity, slug_prefix="ok"
    )

    approval = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version.id,
        target_slug=pack.slug,
        reason="ship it",
    )

    assert approval.resource_type == hub_pp_svc.HUB_PROMOTION_RESOURCE_TYPE
    assert approval.resource_id == pack.id
    assert approval.status == ApprovalStatus.PENDING
    assert approval.workspace_id == workspace.id
    assert approval.requested_by_identity_id == identity.id
    assert approval.expires_at is not None

    body = approval.tool_args
    assert body["pack_id"] == str(pack.id)
    assert body["target_scope"] == HubScope.TENANT.value
    assert body["target_slug"] == pack.slug
    assert body["sanitized_content_hash"]
    assert body["sanitization_stats"]["failure_reason"] is None

    actions = (
        (
            await db_session.execute(
                select(AuditEvent.action).where(AuditEvent.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert hub_pp_svc.AUDIT_PROMOTION_PROPOSED in actions


async def test_platform_scope_allowed_for_platform_admin(db_session, identity):
    workspace, pack, version = await _make_pack_and_version(
        db_session, identity=identity, slug_prefix="padm"
    )
    identity.platform_role = PlatformRole.PLATFORM_ADMIN
    await db_session.flush([identity])

    approval = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.PLATFORM,
        actor=identity,
        version_id=version.id,
    )
    assert approval.tool_args["target_scope"] == HubScope.PLATFORM.value
    # PLATFORM scope sets tenant_id to NULL.
    assert approval.tool_args["target_tenant_id"] is None
