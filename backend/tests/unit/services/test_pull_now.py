"""Unit: ``hub_pull_push.pull_now`` (M3.3).

Three rules under test:

* Up-to-date subscription → no-op result with ``status='up_to_date'``;
  no new local SkillPackVersion is created.
* New hub version → drafts a local
  :class:`~app.db.models.skills.SkillPack(state=DRAFT)` and a
  :class:`~app.db.models.skill_pack_version.SkillPackVersion(state=PROPOSED)`;
  subscription cursor advances.
* Cross-tenant pull is refused — a workspace cannot pull a sibling
  tenant's TENANT-scope pack even if it forged a subscription row.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import NotFound
from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.db.models.skill_pack_version import SkillPackVersionState
from app.db.models.skills import SkillPackState
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import hub_pull_push as hub_pp_svc
from app.services import hub_skill as hub_svc

pytestmark = pytest.mark.asyncio


async def _seed_workspace(db, *, identity, slug_prefix: str):
    from app.services import workspace as ws_svc

    return await ws_svc.create_workspace(
        db,
        name="pull-target",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )


async def _seed_hub_pack_with_version(
    db,
    *,
    tenant_id: uuid.UUID | None,
    scope: HubScope = HubScope.TENANT,
    slug: str | None = None,
    body: str = "# hub body v1",
):
    final_slug = slug or f"hp-{uuid.uuid4().hex[:6]}"
    pack = await HubSkillPackRepository(db).create(
        scope=scope,
        tenant_id=tenant_id,
        slug=final_slug,
        name="Hub pack",
        description=None,
        state=HubSkillPackState.ACTIVE,
        tags=[],
    )
    await db.flush()
    version = await HubSkillPackVersionRepository(db).create(
        hub_pack_id=pack.id,
        version_no=1,
        content_hash=f"hash-{uuid.uuid4().hex}",
        content_md=body,
        files_json={},
        is_active=True,
    )
    await db.flush()
    return pack, version


async def test_up_to_date_returns_noop(db_session, identity):
    workspace = await _seed_workspace(db_session, identity=identity, slug_prefix="up")
    await db_session.flush()
    tenant_id = await hub_svc.resolve_caller_tenant(
        db_session, workspace_id=workspace.id
    )
    hub_pack, hub_version = await _seed_hub_pack_with_version(
        db_session, tenant_id=tenant_id
    )

    sub = await WorkspaceHubSubscriptionRepository(db_session).create(
        workspace_id=workspace.id,
        hub_pack_id=hub_pack.id,
        auto_pull=True,
        last_pulled_version_no=hub_version.version_no,
        last_pulled_at=None,
        subscribed_by_identity_id=identity.id,
    )
    await db_session.flush([sub])

    result = await hub_pp_svc.pull_now(
        db_session,
        workspace_id=workspace.id,
        hub_pack_id=hub_pack.id,
        actor_identity_id=identity.id,
    )

    assert result.status == "up_to_date"
    assert result.local_pack_id is None

    # No local SkillPack was created.
    local = await SkillPackRepository(db_session).get_by_slug(
        workspace_id=workspace.id, slug=hub_pack.slug
    )
    assert local is None


async def test_pull_drafts_local_proposed_version(db_session, identity):
    workspace = await _seed_workspace(db_session, identity=identity, slug_prefix="dn")
    await db_session.flush()
    tenant_id = await hub_svc.resolve_caller_tenant(
        db_session, workspace_id=workspace.id
    )
    hub_pack, hub_version = await _seed_hub_pack_with_version(
        db_session, tenant_id=tenant_id, body="# new candidate from hub"
    )

    sub = await WorkspaceHubSubscriptionRepository(db_session).create(
        workspace_id=workspace.id,
        hub_pack_id=hub_pack.id,
        auto_pull=False,  # manual pull path
        last_pulled_version_no=None,
        last_pulled_at=None,
        subscribed_by_identity_id=identity.id,
    )
    await db_session.flush([sub])

    result = await hub_pp_svc.pull_now(
        db_session,
        workspace_id=workspace.id,
        hub_pack_id=hub_pack.id,
        actor_identity_id=identity.id,
    )

    assert result.status == "pulled"
    assert result.local_pack_id is not None
    assert result.local_version_id is not None

    local_pack = await SkillPackRepository(db_session).get(result.local_pack_id)
    assert local_pack is not None
    assert local_pack.state == SkillPackState.DRAFT
    assert local_pack.workspace_id == workspace.id
    assert local_pack.slug == hub_pack.slug
    # Disabled by default — runtime injection should not pick it up
    # until M2.4 verifier + admin activation.
    assert local_pack.enabled is False

    local_version = await SkillPackVersionRepository(db_session).get(
        result.local_version_id
    )
    assert local_version is not None
    assert local_version.state == SkillPackVersionState.PROPOSED
    assert local_version.created_by == "hub_pull"
    assert local_version.source_run_ids == []
    assert local_version.content_md == "# new candidate from hub"
    assert local_version.content_hash == hub_version.content_hash

    # Subscription cursor advanced.
    await db_session.refresh(sub)
    assert sub.last_pulled_version_no == hub_version.version_no
    assert sub.last_pulled_at is not None


async def test_cross_tenant_pull_blocked(db_session, identity):
    """Even with a stale subscription row, the visibility cut on the
    hub pack stops a tenant from pulling a sibling-tenant pack.
    """
    workspace_a = await _seed_workspace(
        db_session, identity=identity, slug_prefix="a"
    )
    workspace_b = await _seed_workspace(
        db_session, identity=identity, slug_prefix="b"
    )
    await db_session.flush()
    tenant_a = await hub_svc.resolve_caller_tenant(
        db_session, workspace_id=workspace_a.id
    )

    # Hub pack belongs to tenant A only.
    hub_pack, _v = await _seed_hub_pack_with_version(
        db_session, tenant_id=tenant_a
    )

    # Forge a subscription from tenant B.
    sub = await WorkspaceHubSubscriptionRepository(db_session).create(
        workspace_id=workspace_b.id,
        hub_pack_id=hub_pack.id,
        auto_pull=True,
        last_pulled_version_no=None,
        last_pulled_at=None,
        subscribed_by_identity_id=identity.id,
    )
    await db_session.flush([sub])

    with pytest.raises(NotFound) as exc_info:
        await hub_pp_svc.pull_now(
            db_session,
            workspace_id=workspace_b.id,
            hub_pack_id=hub_pack.id,
            actor_identity_id=identity.id,
        )
    assert exc_info.value.code == "hub.pack_not_found"
