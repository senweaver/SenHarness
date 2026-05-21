"""Integration: ``hub_auto_pull_sweep`` end-to-end (M3.3).

Two rules under test:

* Multiple workspaces × multiple subscriptions with ``auto_pull=True``
  all get pulled in a single tick — local DRAFT pack +
  PROPOSED version land for each subscription.
* Subscriptions with ``auto_pull=False`` are skipped (the manual
  *Pull now* button is the only way they land a candidate).
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.db.models.skill_pack_version import SkillPackVersionState
from app.db.models.skills import SkillPackState
from app.db.session import get_session_factory
from app.jobs.hub_auto_pull import hub_auto_pull_sweep
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import hub_skill as hub_svc

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[str, str]:
    email = f"hap-{uuid.uuid4().hex[:8]}@example.com"
    password = "hub-auto-pull-pw-extremely-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Auto Pull", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    return workspace["id"], str(body["identity_id"])


async def _seed_hub_pack(
    *, scope: str, tenant_id: str | None, slug: str, body: str
) -> tuple[str, int]:
    """Seed a hub pack + active version. Returns (hub_pack_id, version_no)."""
    factory = get_session_factory()
    async with factory() as db:
        pack = await HubSkillPackRepository(db).create(
            scope=HubScope(scope),
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
            slug=slug,
            name="auto-pull seed",
            description=None,
            state=HubSkillPackState.ACTIVE,
            tags=[],
        )
        await db.flush([pack])
        version = await HubSkillPackVersionRepository(db).create(
            hub_pack_id=pack.id,
            version_no=1,
            content_hash=f"hashap-{uuid.uuid4().hex}",
            content_md=body,
            files_json={},
            is_active=True,
        )
        await db.flush([version])
        await db.commit()
        return str(pack.id), version.version_no


async def _seed_subscription(
    *, workspace_id: str, hub_pack_id: str, auto_pull: bool, identity_id: str
) -> str:
    factory = get_session_factory()
    async with factory() as db:
        sub = await WorkspaceHubSubscriptionRepository(db).create(
            workspace_id=uuid.UUID(workspace_id),
            hub_pack_id=uuid.UUID(hub_pack_id),
            auto_pull=auto_pull,
            last_pulled_version_no=None,
            last_pulled_at=None,
            subscribed_by_identity_id=uuid.UUID(identity_id),
        )
        await db.commit()
        return str(sub.id)


async def _resolve_tenant(workspace_id: str) -> str:
    factory = get_session_factory()
    async with factory() as db:
        tid = await hub_svc.resolve_caller_tenant(
            db, workspace_id=uuid.UUID(workspace_id)
        )
    return str(tid) if tid else ""


async def test_auto_pull_sweep_pulls_all_subscribed_workspaces(async_client):
    ws_a, id_a = await _bootstrap(async_client)
    ws_b, id_b = await _bootstrap(async_client)
    tenant_a = await _resolve_tenant(ws_a)
    tenant_b = await _resolve_tenant(ws_b)

    pack_a, _ = await _seed_hub_pack(
        scope="tenant",
        tenant_id=tenant_a,
        slug=f"a-{uuid.uuid4().hex[:6]}",
        body="# tenant A hub body",
    )
    pack_b, _ = await _seed_hub_pack(
        scope="tenant",
        tenant_id=tenant_b,
        slug=f"b-{uuid.uuid4().hex[:6]}",
        body="# tenant B hub body",
    )
    await _seed_subscription(
        workspace_id=ws_a, hub_pack_id=pack_a, auto_pull=True, identity_id=id_a
    )
    await _seed_subscription(
        workspace_id=ws_b, hub_pack_id=pack_b, auto_pull=True, identity_id=id_b
    )

    summary = await hub_auto_pull_sweep({})

    # Both subscriptions pulled. The summary may include other
    # workspaces created by sibling tests; we only assert ours land
    # in the counters.
    assert summary["status"] == "ok"
    assert summary["subscriptions_pulled"] >= 2

    # Per-workspace local SkillPack(state=DRAFT) + Version(state=PROPOSED).
    factory = get_session_factory()
    async with factory() as db:
        for ws_id, hub_pack_id in ((ws_a, pack_a), (ws_b, pack_b)):
            sub = await WorkspaceHubSubscriptionRepository(db).get_by_pack(
                workspace_id=uuid.UUID(ws_id),
                hub_pack_id=uuid.UUID(hub_pack_id),
            )
            assert sub is not None
            assert sub.last_pulled_version_no == 1

            local_packs = await SkillPackRepository(db).list_for_workspace(
                workspace_id=uuid.UUID(ws_id)
            )
            assert any(
                lp.state == SkillPackState.DRAFT
                and (lp.metadata_json or {}).get("hub", {}).get("hub_pack_id")
                == hub_pack_id
                for lp in local_packs
            )

            local_versions: list = []
            for lp in local_packs:
                if (lp.metadata_json or {}).get("hub", {}).get(
                    "hub_pack_id"
                ) == hub_pack_id:
                    versions = await SkillPackVersionRepository(db).list_for_pack(
                        workspace_id=uuid.UUID(ws_id), pack_id=lp.id
                    )
                    local_versions.extend(versions)
            assert local_versions
            assert all(
                v.state == SkillPackVersionState.PROPOSED for v in local_versions
            )


async def test_auto_pull_sweep_skips_disabled_subscriptions(async_client):
    ws, identity_id = await _bootstrap(async_client)
    tenant_id = await _resolve_tenant(ws)
    hub_pack_id, _ = await _seed_hub_pack(
        scope="tenant",
        tenant_id=tenant_id,
        slug=f"off-{uuid.uuid4().hex[:6]}",
        body="# auto_pull=False body",
    )
    await _seed_subscription(
        workspace_id=ws,
        hub_pack_id=hub_pack_id,
        auto_pull=False,
        identity_id=identity_id,
    )

    await hub_auto_pull_sweep({})

    factory = get_session_factory()
    async with factory() as db:
        sub = await WorkspaceHubSubscriptionRepository(db).get_by_pack(
            workspace_id=uuid.UUID(ws),
            hub_pack_id=uuid.UUID(hub_pack_id),
        )
        assert sub is not None
        # Cursor never advanced — the sweep didn't touch this row.
        assert sub.last_pulled_version_no is None

        local = await SkillPackRepository(db).list_for_workspace(
            workspace_id=uuid.UUID(ws)
        )
        # No DRAFT pack from this hub.
        assert not any(
            (lp.metadata_json or {}).get("hub", {}).get("hub_pack_id")
            == hub_pack_id
            and lp.state == SkillPackState.DRAFT
            for lp in local
        )
