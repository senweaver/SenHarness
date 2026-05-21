"""Integration: ``hub_promote_pipeline.preview_promotion`` (M3.2).

Three rules under test:

1. End-to-end sanitize: a SkillPack version body containing emails,
   workspace-slug URLs, and bare slug references gets its sanitized
   counterpart in :class:`HubPromotionPreview.sanitized.content_md`,
   plus stats counters that match the manual count.
2. ``HubSettings.sanitizer_required=True`` + sanitize failure →
   ``BLOCKER_SANITIZER_REQUIRED_FAILED`` lands in ``preview.blockers``
   and the audit feed gets an extra ``hub.sanitize.blocked_by_required``
   row.
3. Same sanitized hash already on hub → ``preview.will_dedup_against``
   points at the existing :class:`HubSkillPackVersion`.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.db.models.skills import SkillPackState
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
)
from app.repositories.skills import SkillPackRepository
from app.services import hub_promote_pipeline as pipeline
from app.services import hub_skill as hub_svc
from app.services import platform_settings as platform_settings_svc
from app.services import skill_version as skill_version_svc
from app.services.skill_sanitize import (
    EMAIL_PLACEHOLDER,
    WORKSPACE_SLUG_PLACEHOLDER,
)

pytestmark = pytest.mark.asyncio


_DIRTY_BODY = (
    "# How to onboard\n\n"
    "Owner: alice@example.com\n"
    "Docs: https://example.com/myteam/setup\n"
    "Run `/data/myteam/runtime/log.txt` to inspect.\n"
    "Ping ops at ops@example.org\n"
)


async def _make_workspace_pack(db, *, identity, slug: str = "myteam"):
    from app.services import workspace as ws_svc

    workspace = await ws_svc.create_workspace(
        db,
        name="Promotion test",
        slug=slug,
        owner_identity_id=identity.id,
    )
    await db.flush()
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace.id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Onboard skill",
        description="x",
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
        content_md=_DIRTY_BODY,
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
        source_run_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
    )
    await db.flush()
    return workspace, pack, version


async def test_preview_redacts_emails_urls_and_slug(db_session, identity):
    workspace, pack, version = await _make_workspace_pack(db_session, identity=identity)
    platform_settings_svc.invalidate_cache(  # type: ignore[attr-defined]
        platform_settings_svc.PlatformSettingsSection.HUB
    ) if hasattr(platform_settings_svc, "invalidate_cache") else None

    preview = await pipeline.preview_promotion(
        db_session,
        request=pipeline.HubPromotionInput(
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_scope=HubScope.TENANT,
            version_id=version.id,
            target_slug=pack.slug,
        ),
        actor_identity=identity,
    )

    assert preview.is_promotable is True, preview.blockers
    body = preview.sanitized.content_md
    assert "alice@example.com" not in body
    assert "ops@example.org" not in body
    assert body.count(EMAIL_PLACEHOLDER) == 2
    assert "myteam" not in body
    assert WORKSPACE_SLUG_PLACEHOLDER in body
    assert preview.sanitized.stats.redacted_emails == 2
    assert preview.sanitized.stats.redacted_urls == 1
    assert preview.sanitized.stats.redacted_paths >= 1
    assert preview.sanitized.stats.run_id_hashed_count == 2
    assert preview.target_tenant_id == workspace.id  # tenant fallback
    assert preview.sanitized_content_hash != version.content_hash
    assert preview.will_dedup_against is None


async def test_sanitizer_required_failed_blocks_when_setting_on(db_session, identity):
    workspace, pack, version = await _make_workspace_pack(
        db_session, identity=identity, slug=f"ws-{uuid.uuid4().hex[:6]}"
    )

    target_path = "app.services.hub_promote_pipeline.sanitize_for_hub"
    from app.services.skill_sanitize import (
        SanitizationStats,
        SanitizedHubPayload,
    )

    def _fake_sanitize(body, run_ids, **kwargs):
        return SanitizedHubPayload(
            content_md=body,
            source_run_id_hashes=[],
            stats=SanitizationStats(failure_reason="boom"),
        )

    with patch(target_path, side_effect=_fake_sanitize):
        preview = await pipeline.preview_promotion(
            db_session,
            request=pipeline.HubPromotionInput(
                workspace_id=workspace.id,
                pack_id=pack.id,
                target_scope=HubScope.TENANT,
                version_id=version.id,
                target_slug=pack.slug,
            ),
            actor_identity=identity,
        )

    assert pipeline.BLOCKER_SANITIZER_REQUIRED_FAILED in preview.blockers
    assert preview.is_promotable is False
    actions = (
        (
            await db_session.execute(
                select(AuditEvent.action).where(AuditEvent.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert "hub.sanitize.failed" in actions
    assert "hub.sanitize.blocked_by_required" in actions


async def test_dedup_target_returned_when_hash_already_on_hub(db_session, identity):
    workspace, pack, version = await _make_workspace_pack(
        db_session, identity=identity, slug=f"ws-{uuid.uuid4().hex[:6]}"
    )
    target_slug = pack.slug

    preview = await pipeline.preview_promotion(
        db_session,
        request=pipeline.HubPromotionInput(
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_scope=HubScope.TENANT,
            version_id=version.id,
            target_slug=target_slug,
        ),
        actor_identity=identity,
    )
    sanitized_hash = preview.sanitized_content_hash

    tenant_id = await hub_svc.resolve_caller_tenant(db_session, workspace_id=workspace.id)
    hub_repo = HubSkillPackRepository(db_session)
    hub_pack = await hub_repo.create(
        scope=HubScope.TENANT,
        tenant_id=tenant_id,
        slug=target_slug,
        name="hub copy",
        description=None,
        state=HubSkillPackState.ACTIVE,
        tags=[],
    )
    await db_session.flush()
    await HubSkillPackVersionRepository(db_session).create(
        hub_pack_id=hub_pack.id,
        version_no=1,
        content_hash=sanitized_hash,
        content_md=preview.sanitized.content_md,
        files_json={},
        is_active=True,
    )
    await db_session.flush()

    preview2 = await pipeline.preview_promotion(
        db_session,
        request=pipeline.HubPromotionInput(
            workspace_id=workspace.id,
            pack_id=pack.id,
            target_scope=HubScope.TENANT,
            version_id=version.id,
            target_slug=target_slug,
        ),
        actor_identity=identity,
    )
    assert preview2.will_dedup_against is not None
    assert preview2.will_dedup_against.content_hash == sanitized_hash


async def test_pack_not_owned_by_workspace_returns_blocker(db_session, identity):
    workspace_a, _pack_a, _version_a = await _make_workspace_pack(
        db_session, identity=identity, slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    _workspace_b, pack_b, _version_b = await _make_workspace_pack(
        db_session, identity=identity, slug=f"b-{uuid.uuid4().hex[:6]}"
    )

    preview = await pipeline.preview_promotion(
        db_session,
        request=pipeline.HubPromotionInput(
            workspace_id=workspace_a.id,
            pack_id=pack_b.id,
            target_scope=HubScope.TENANT,
        ),
        actor_identity=identity,
    )
    assert pipeline.BLOCKER_PACK_NOT_OWNED in preview.blockers
