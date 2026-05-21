"""Integration: M3.3 hub promote / subscribe / pull REST surface.

5 endpoints × happy + RBAC + cross-tenant isolation:

* POST   /skills/packs/{pack_id}/promote-to-hub
* POST   /skills/hub/{hub_pack_id}/subscribe
* DELETE /skills/hub/{hub_pack_id}/subscribe
* POST   /skills/hub/{hub_pack_id}/pull
* GET    /skills/hub/{hub_pack_id}/subscription-status

The promote verb leaves the actual hub commit to the M2.5 dispatch
handler — see ``test_hub_promotion_full_loop.py`` for the
propose-→-approve-→-apply chain.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.db.session import get_session_factory

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    email = f"pp-{uuid.uuid4().hex[:8]}@example.com"
    password = "pull-push-very-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "PP Tester", "password": password},
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
    ws_id = workspace.get("id")
    headers = {"Authorization": f"Bearer {token}"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id, str(body["identity_id"])


async def _seed_skill_pack(*, ws_id: str, body: str = "# unit test body") -> str:
    factory = get_session_factory()
    from app.db.models.skills import SkillPackSource, SkillPackState
    from app.repositories.skills import SkillPackRepository
    from app.services import skill_version as skill_version_svc

    async with factory() as db:
        pack = await SkillPackRepository(db).create(
            workspace_id=uuid.UUID(ws_id),
            slug=f"pp-{uuid.uuid4().hex[:6]}",
            name="PP source pack",
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
            content_md=body,
            files=None,
            created_by="user",
            creator_identity_id=None,
            source_run_ids=[str(uuid.uuid4())],
        )
        await db.commit()
        return str(pack.id)


async def _seed_hub_pack(
    *, scope: str, tenant_id: str | None, slug: str, body: str = "# hub body"
) -> str:
    factory = get_session_factory()
    from app.repositories.hub_skill_pack import (
        HubSkillPackRepository,
        HubSkillPackVersionRepository,
    )

    async with factory() as db:
        pack = await HubSkillPackRepository(db).create(
            scope=HubScope(scope),
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
            slug=slug,
            name="hub seed",
            description=None,
            state=HubSkillPackState.ACTIVE,
            tags=[],
        )
        await db.flush([pack])
        await HubSkillPackVersionRepository(db).create(
            hub_pack_id=pack.id,
            version_no=1,
            content_hash=f"hashpp-{uuid.uuid4().hex}",
            content_md=body,
            files_json={},
            is_active=True,
        )
        await db.commit()
        return str(pack.id)


# ── 1. POST /skills/packs/{id}/promote-to-hub ───────────────
async def test_promote_happy_creates_pending_approval(async_client):
    headers, ws_id, _identity_id = await _bootstrap(async_client)
    pack_id = await _seed_skill_pack(ws_id=ws_id)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pack_id}/promote-to-hub",
        headers=headers,
        json={"target_scope": "tenant"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["pack_id"] == pack_id
    assert body["target_scope"] == "tenant"
    assert body["sanitized_content_hash"]
    assert body["expires_at"]

    # Approval row is pending.
    factory = get_session_factory()
    from app.db.models.approval import Approval, ApprovalStatus

    async with factory() as db:
        from sqlalchemy import select

        row = (
            await db.execute(
                select(Approval).where(Approval.id == uuid.UUID(body["approval_id"]))
            )
        ).scalar_one()
        assert row.status == ApprovalStatus.PENDING
        assert row.resource_type == "hub_promotion"


async def test_promote_platform_scope_blocked_for_non_platform_admin(
    async_client,
):
    headers, ws_id, _ = await _bootstrap(async_client)
    pack_id = await _seed_skill_pack(ws_id=ws_id)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pack_id}/promote-to-hub",
        headers=headers,
        json={"target_scope": "platform"},
    )
    assert r.status_code == 403
    detail = r.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else r.json().get("code")
    assert code == "hub.scope_permission_denied"


async def test_promote_cross_workspace_pack_returns_409(async_client):
    """Workspace A's admin cannot promote workspace B's pack."""
    headers_a, ws_a, _ = await _bootstrap(async_client)
    _headers_b, ws_b, _ = await _bootstrap(async_client)
    pack_b = await _seed_skill_pack(ws_id=ws_b)

    r = await async_client.post(
        f"/api/v1/skills/packs/{pack_b}/promote-to-hub",
        headers=headers_a,
        json={"target_scope": "tenant"},
    )
    assert r.status_code == 409
    detail = r.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else r.json().get("code")
    assert code == "hub.promotion_blocked"


# ── 2. POST /skills/hub/{id}/subscribe ──────────────────────
async def test_subscribe_happy(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_id, slug=f"sub-{uuid.uuid4().hex[:6]}"
    )

    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
        json={"auto_pull": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub_pack_id"] == hub_pack
    assert body["auto_pull"] is True


async def test_subscribe_cross_tenant_blocked(async_client):
    headers_a, ws_a, _ = await _bootstrap(async_client)
    headers_b, ws_b, _ = await _bootstrap(async_client)
    # Pack belongs to A's tenant; B can't subscribe to it.
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_a, slug=f"xt-{uuid.uuid4().hex[:6]}"
    )

    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers_b,
        json={"auto_pull": True},
    )
    assert r.status_code == 404
    detail = r.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else r.json().get("code")
    assert code == "hub.pack_not_found"


# ── 3. DELETE /skills/hub/{id}/subscribe ────────────────────
async def test_unsubscribe_happy(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_id, slug=f"del-{uuid.uuid4().hex[:6]}"
    )
    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
        json={"auto_pull": False},
    )
    assert r.status_code == 200

    r = await async_client.request(
        "DELETE",
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
    )
    assert r.status_code == 204

    # Re-deleting is 404.
    r = await async_client.request(
        "DELETE",
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
    )
    assert r.status_code == 404


# ── 4. POST /skills/hub/{id}/pull ───────────────────────────
async def test_pull_happy_creates_local_proposed_version(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant",
        tenant_id=ws_id,
        slug=f"pl-{uuid.uuid4().hex[:6]}",
        body="# pulled body",
    )
    # Subscribe first — pull requires it.
    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
        json={"auto_pull": False},
    )
    assert r.status_code == 200

    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/pull",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pulled"
    assert body["local_pack_id"]
    assert body["local_version_id"]


async def test_pull_without_subscription_404(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_id, slug=f"nosub-{uuid.uuid4().hex[:6]}"
    )

    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/pull",
        headers=headers,
    )
    assert r.status_code == 404
    detail = r.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else r.json().get("code")
    assert code == "hub.subscription_not_found"


# ── 5. GET /skills/hub/{id}/subscription-status ─────────────
async def test_subscription_status_unsubscribed(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_id, slug=f"st-{uuid.uuid4().hex[:6]}"
    )

    r = await async_client.get(
        f"/api/v1/skills/hub/{hub_pack}/subscription-status",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["subscribed"] is False
    assert body["subscription"] is None
    assert body["hub_active_version_no"] == 1
    assert body["has_update_available"] is False


async def test_subscription_status_subscribed_with_update(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    hub_pack = await _seed_hub_pack(
        scope="tenant", tenant_id=ws_id, slug=f"upd-{uuid.uuid4().hex[:6]}"
    )

    r = await async_client.post(
        f"/api/v1/skills/hub/{hub_pack}/subscribe",
        headers=headers,
        json={"auto_pull": True},
    )
    assert r.status_code == 200

    r = await async_client.get(
        f"/api/v1/skills/hub/{hub_pack}/subscription-status",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["subscribed"] is True
    assert body["subscription"]["auto_pull"] is True
    assert body["has_update_available"] is True
