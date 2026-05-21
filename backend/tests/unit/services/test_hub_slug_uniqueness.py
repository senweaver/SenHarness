"""Unit: hub slug uniqueness + tombstone reuse gate (M3.1).

* The unique constraint on ``(scope, tenant_id, slug)`` blocks two
  packs with identical keys but allows two tenants to ship the same
  slug.
* Two PLATFORM-scope packs cannot share a slug (covered by the
  partial unique index on ``tenant_id IS NULL``).
* :func:`is_hub_slug_tombstoned` returns True after a pack lands in
  TOMBSTONE state, scoped per (scope, tenant_id) bucket.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.repositories.hub_skill_pack import HubSkillPackRepository
from app.services import hub_skill as hub_svc

pytestmark = pytest.mark.asyncio


async def _make_pack(
    db,
    *,
    scope: HubScope,
    tenant_id: uuid.UUID | None,
    slug: str,
    state: HubSkillPackState = HubSkillPackState.ACTIVE,
):
    pack = await HubSkillPackRepository(db).create(
        scope=scope,
        tenant_id=tenant_id,
        slug=slug,
        name=f"hub pack {slug}",
        state=state,
        tags=[],
    )
    await db.flush()
    return pack


async def test_same_tenant_slug_collides(db_session):
    tenant_id = uuid.uuid4()
    slug = f"shared-{uuid.uuid4().hex[:6]}"
    await _make_pack(
        db_session, scope=HubScope.TENANT, tenant_id=tenant_id, slug=slug
    )
    with pytest.raises(IntegrityError):
        await _make_pack(
            db_session, scope=HubScope.TENANT, tenant_id=tenant_id, slug=slug
        )
    await db_session.rollback()


async def test_different_tenants_share_slug_ok(db_session):
    slug = f"shared-{uuid.uuid4().hex[:6]}"
    a = await _make_pack(
        db_session,
        scope=HubScope.TENANT,
        tenant_id=uuid.uuid4(),
        slug=slug,
    )
    b = await _make_pack(
        db_session,
        scope=HubScope.TENANT,
        tenant_id=uuid.uuid4(),
        slug=slug,
    )
    assert a.id != b.id
    assert a.slug == b.slug == slug


async def test_two_platform_packs_with_same_slug_collide(db_session):
    slug = f"plat-{uuid.uuid4().hex[:6]}"
    await _make_pack(
        db_session, scope=HubScope.PLATFORM, tenant_id=None, slug=slug
    )
    # The migration adds a partial UNIQUE index on (slug) WHERE
    # scope='platform' AND tenant_id IS NULL. The plain unique
    # constraint won't catch it because NULL is distinct in UNIQUE.
    with pytest.raises(IntegrityError):
        await _make_pack(
            db_session, scope=HubScope.PLATFORM, tenant_id=None, slug=slug
        )
    await db_session.rollback()


async def test_tombstoned_slug_blocks_reuse_within_scope(db_session, identity):
    tenant_id = uuid.uuid4()
    slug = f"tomb-{uuid.uuid4().hex[:6]}"
    pack = await _make_pack(
        db_session,
        scope=HubScope.TENANT,
        tenant_id=tenant_id,
        slug=slug,
        state=HubSkillPackState.ARCHIVED,
    )

    from app.db.models.identity import Identity, PlatformRole

    # Promote our test identity to platform admin so the service
    # transition gate accepts the call regardless of scope.
    identity.platform_role = PlatformRole.PLATFORM_ADMIN
    await db_session.flush([identity])

    await hub_svc.transition_hub_pack_state(
        db_session,
        hub_pack_id=pack.id,
        target_state=HubSkillPackState.TOMBSTONE,
        actor=identity,
        reason="prune",
    )
    await db_session.flush()

    assert (
        await hub_svc.is_hub_slug_tombstoned(
            db_session, scope=HubScope.TENANT, tenant_id=tenant_id, slug=slug
        )
        is True
    )
    # Different bucket, same slug → still ok.
    assert (
        await hub_svc.is_hub_slug_tombstoned(
            db_session,
            scope=HubScope.TENANT,
            tenant_id=uuid.uuid4(),
            slug=slug,
        )
        is False
    )
    assert (
        await hub_svc.is_hub_slug_tombstoned(
            db_session,
            scope=HubScope.PLATFORM,
            tenant_id=None,
            slug=slug,
        )
        is False
    )

    # Silence unused-import — the Identity reference is for the
    # platform_role mutation above.
    _ = Identity
