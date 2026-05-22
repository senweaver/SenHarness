"""Unit: hub catalog visibility (M3.1).

Three rules:

* PLATFORM-scope packs are visible to every workspace.
* TENANT-scope packs are only visible to workspaces whose resolved
  tenant id matches the pack's ``tenant_id``.
* No tenants column today — :func:`resolve_caller_tenant` falls back
  to ``workspace.id`` so a single-workspace tenant gets the natural
  isolation. Two distinct workspaces therefore never see each
  other's TENANT-scope rows.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.repositories.hub_skill_pack import HubSkillPackRepository
from app.services import hub_skill as hub_svc
from app.services import workspace as ws_svc

pytestmark = pytest.mark.asyncio


async def _make_hub_pack(
    db,
    *,
    scope: HubScope,
    tenant_id: uuid.UUID | None,
    slug: str,
    state: HubSkillPackState = HubSkillPackState.ACTIVE,
):
    repo = HubSkillPackRepository(db)
    pack = await repo.create(
        scope=scope,
        tenant_id=tenant_id,
        slug=slug,
        name=f"hub pack {slug}",
        description=None,
        state=state,
        tags=[],
    )
    await db.flush()
    return pack


async def test_resolve_caller_tenant_falls_back_to_workspace_id(db_session, workspace):
    tenant_id = await hub_svc.resolve_caller_tenant(db_session, workspace_id=workspace.id)
    assert tenant_id == workspace.id


async def test_platform_scope_is_visible_to_every_workspace(db_session, workspace, identity):
    other_ws = await ws_svc.create_workspace(
        db_session,
        name="Other",
        slug=f"other-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    slug = f"platform-{uuid.uuid4().hex[:6]}"
    pack = await _make_hub_pack(db_session, scope=HubScope.PLATFORM, tenant_id=None, slug=slug)

    rows_a = await hub_svc.list_hub_catalog(db_session, workspace_id=workspace.id, limit=200)
    rows_b = await hub_svc.list_hub_catalog(db_session, workspace_id=other_ws.id, limit=200)
    assert pack.id in {r.id for r in rows_a}
    assert pack.id in {r.id for r in rows_b}


async def test_tenant_scope_visible_only_to_matching_tenant(db_session, workspace, identity):
    other_ws = await ws_svc.create_workspace(
        db_session,
        name="Other tenant",
        slug=f"oth-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    slug = f"tenant-{uuid.uuid4().hex[:6]}"
    pack = await _make_hub_pack(
        db_session,
        scope=HubScope.TENANT,
        tenant_id=workspace.id,  # tenant fallback == workspace.id
        slug=slug,
    )

    rows_a = await hub_svc.list_hub_catalog(db_session, workspace_id=workspace.id, limit=200)
    rows_b = await hub_svc.list_hub_catalog(db_session, workspace_id=other_ws.id, limit=200)
    assert pack.id in {r.id for r in rows_a}
    assert pack.id not in {r.id for r in rows_b}


async def test_get_by_id_visible_blocks_cross_tenant(db_session, workspace, identity):
    other_ws = await ws_svc.create_workspace(
        db_session,
        name="Other",
        slug=f"oth-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    slug = f"hidden-{uuid.uuid4().hex[:6]}"
    pack = await _make_hub_pack(
        db_session,
        scope=HubScope.TENANT,
        tenant_id=workspace.id,
        slug=slug,
    )

    repo = HubSkillPackRepository(db_session)
    visible_to_owner = await repo.get_by_id_visible(
        hub_pack_id=pack.id,
        workspace_id=workspace.id,
        tenant_id=workspace.id,
    )
    visible_to_stranger = await repo.get_by_id_visible(
        hub_pack_id=pack.id,
        workspace_id=other_ws.id,
        tenant_id=other_ws.id,
    )
    assert visible_to_owner is not None
    assert visible_to_stranger is None


async def test_archived_state_hidden_from_default_listing(db_session, workspace):
    slug = f"arch-{uuid.uuid4().hex[:6]}"
    pack = await _make_hub_pack(
        db_session,
        scope=HubScope.PLATFORM,
        tenant_id=None,
        slug=slug,
        state=HubSkillPackState.ARCHIVED,
    )

    rows = await hub_svc.list_hub_catalog(db_session, workspace_id=workspace.id, limit=200)
    assert pack.id not in {r.id for r in rows}

    explicit = await hub_svc.list_hub_catalog(
        db_session,
        workspace_id=workspace.id,
        state_filter=HubSkillPackState.ARCHIVED,
        limit=200,
    )
    assert pack.id in {r.id for r in explicit}
