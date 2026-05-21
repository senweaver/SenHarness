"""Unit: ``hub_pull_push.apply_promotion`` (M3.3).

Three rules under test:

* New hub pack — apply path inserts both
  :class:`HubSkillPack` and an active
  :class:`HubSkillPackVersion`, plus back-subscribes the source
  workspace with ``auto_pull=True``.
* Dedup hit — same sanitized hash already on the hub for the
  resolved slug → no new version row, the existing one becomes
  ``is_active=True`` (or stays so), and ``deduped`` flag is True
  in the result envelope.
* Existing slug, new content — second promote of an updated body
  appends a new version_no, retires the previous active row, and
  reuses the same hub_pack row.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.hub_skill_pack import HubScope
from app.db.models.skills import SkillPackState
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.repositories.skills import SkillPackRepository
from app.services import hub_pull_push as hub_pp_svc
from app.services import skill_version as skill_version_svc

pytestmark = pytest.mark.asyncio


async def _seed_pack_with_version(db, *, identity, body: str, slug_prefix: str):
    from app.services import workspace as ws_svc

    workspace = await ws_svc.create_workspace(
        db,
        name="apply-promo",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db.flush()
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace.id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Apply pack",
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
        content_md=body,
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
        source_run_ids=[str(uuid.uuid4())],
    )
    await db.flush()
    return workspace, pack, version


async def test_apply_creates_new_hub_pack_and_subscription(
    db_session, identity
):
    workspace, pack, version = await _seed_pack_with_version(
        db_session, identity=identity, body="# fresh content", slug_prefix="new"
    )

    approval = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version.id,
    )
    await db_session.flush()

    result = await hub_pp_svc.apply_promotion(
        db_session,
        approval_id=approval.id,
        actor_identity_id=identity.id,
    )

    assert result["deduped"] is False
    assert result["hub_version_no"] == 1

    hub_pack = await HubSkillPackRepository(db_session).get(result["hub_pack_id"])
    assert hub_pack is not None
    assert hub_pack.slug == approval.tool_args["target_slug"]
    assert hub_pack.scope == HubScope.TENANT
    assert hub_pack.tenant_id == workspace.id  # fallback resolution
    assert hub_pack.promoted_from_pack_id == pack.id

    active = await HubSkillPackVersionRepository(db_session).get_active(
        hub_pack_id=hub_pack.id
    )
    assert active is not None
    assert active.id == result["hub_version_id"]
    assert active.is_active is True
    assert active.promoted_from_workspace_version_id == version.id

    sub = await WorkspaceHubSubscriptionRepository(db_session).get_by_pack(
        workspace_id=workspace.id, hub_pack_id=hub_pack.id
    )
    assert sub is not None
    assert sub.auto_pull is True
    assert sub.last_pulled_version_no == 1


async def test_apply_dedup_does_not_create_new_version(db_session, identity):
    workspace, pack, version = await _seed_pack_with_version(
        db_session, identity=identity, body="# dedupable", slug_prefix="dedup"
    )

    # First promote: creates the hub pack + first version.
    approval1 = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version.id,
    )
    await db_session.flush()
    first = await hub_pp_svc.apply_promotion(
        db_session,
        approval_id=approval1.id,
        actor_identity_id=identity.id,
    )
    await db_session.flush()
    hub_pack_id = first["hub_pack_id"]

    # Second promote of the same source version → preview returns
    # ``will_dedup_against`` non-None.
    approval2 = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version.id,
    )
    await db_session.flush()
    second = await hub_pp_svc.apply_promotion(
        db_session,
        approval_id=approval2.id,
        actor_identity_id=identity.id,
    )

    assert second["hub_pack_id"] == hub_pack_id
    assert second["deduped"] is True
    assert second["hub_version_no"] == first["hub_version_no"]
    assert second["hub_version_id"] == first["hub_version_id"]


async def test_apply_existing_slug_appends_new_version(db_session, identity):
    workspace, pack, version_a = await _seed_pack_with_version(
        db_session, identity=identity, body="# v1 body", slug_prefix="grow"
    )

    approval1 = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version_a.id,
    )
    await db_session.flush()
    first = await hub_pp_svc.apply_promotion(
        db_session,
        approval_id=approval1.id,
        actor_identity_id=identity.id,
    )
    await db_session.flush()

    # Author a second local version with different bytes, then
    # promote it under the same slug bucket.
    version_b = await skill_version_svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="# v2 body — totally different",
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
        source_run_ids=[str(uuid.uuid4())],
    )
    await db_session.flush()

    approval2 = await hub_pp_svc.initiate_promotion(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        target_scope=HubScope.TENANT,
        actor=identity,
        version_id=version_b.id,
    )
    await db_session.flush()
    second = await hub_pp_svc.apply_promotion(
        db_session,
        approval_id=approval2.id,
        actor_identity_id=identity.id,
    )

    assert second["hub_pack_id"] == first["hub_pack_id"]
    assert second["deduped"] is False
    assert second["hub_version_no"] == first["hub_version_no"] + 1

    # Single is_active=True invariant.
    repo = HubSkillPackVersionRepository(db_session)
    active = await repo.get_active(hub_pack_id=second["hub_pack_id"])
    assert active is not None
    assert active.id == second["hub_version_id"]

    all_versions = await repo.list_for_pack(hub_pack_id=second["hub_pack_id"])
    actives = [v for v in all_versions if v.is_active]
    assert len(actives) == 1
