"""Integration: M3.1 Skill Hub catalog REST surface.

5 endpoints × happy + RBAC + cross-tenant isolation:

* GET    /skills/hub                                   list catalog
* GET    /skills/hub/{hub_pack_id}                     pack detail
* GET    /skills/hub/{hub_pack_id}/versions            version list
* GET    /skills/hub/{hub_pack_id}/versions/active     active version body
* POST   /admin/skills/hub/{hub_pack_id}/transition    state machine
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str, str]:
    """Register a user, return ``(headers, workspace_id, identity_id)``."""
    email = f"hub-{uuid.uuid4().hex[:8]}@example.com"
    password = "hub-tester-very-long-password"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Hub Tester", "password": password},
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
    identity_id = body.get("identity", {}).get("id")
    headers = {"Authorization": f"Bearer {token}"}
    if ws_id:
        headers["X-Workspace-Id"] = ws_id
    return headers, ws_id, identity_id


async def _seed_hub_pack(
    *,
    scope: str,
    tenant_id: str | None,
    slug: str,
    name: str = "Test Hub Pack",
    state: str = "active",
) -> str:
    """Insert a hub_skill_pack row directly via the test DB session.

    M3.1 ships no public POST verb (M3.3 promote is not in scope), so
    integration tests seed the catalog at the model layer.
    """
    from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.hub_skill_pack import HubSkillPackRepository

        pack = await HubSkillPackRepository(db).create(
            scope=HubScope(scope),
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
            slug=slug,
            name=name,
            description=None,
            state=HubSkillPackState(state),
            tags=[],
        )
        await db.commit()
        return str(pack.id)


async def _seed_hub_version(*, hub_pack_id: str, version_no: int = 1) -> str:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.hub_skill_pack import (
            HubSkillPackVersionRepository,
        )

        version = await HubSkillPackVersionRepository(db).create(
            hub_pack_id=uuid.UUID(hub_pack_id),
            version_no=version_no,
            content_hash="cafe" + "0" * 60 + str(version_no),
            content_md=f"---\nname: t\ndescription: y\n---\n\nv{version_no}",
            files_json={},
            is_active=True,
        )
        await db.commit()
        return str(version.id)


# ── 1. GET /skills/hub — happy ──────────────────────────────
async def test_list_hub_catalog_returns_platform_pack(async_client):
    headers, _ws, _ = await _bootstrap(async_client)
    slug = f"plat-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="platform", tenant_id=None, slug=slug)

    r = await async_client.get("/api/v1/skills/hub", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(it["id"] == pack_id for it in items), items


async def test_list_hub_catalog_excludes_other_tenant(async_client):
    headers_a, ws_a, _ = await _bootstrap(async_client)
    headers_b, ws_b, _ = await _bootstrap(async_client)

    slug_a = f"ta-{uuid.uuid4().hex[:6]}"
    pack_a = await _seed_hub_pack(scope="tenant", tenant_id=ws_a, slug=slug_a)
    slug_b = f"tb-{uuid.uuid4().hex[:6]}"
    pack_b = await _seed_hub_pack(scope="tenant", tenant_id=ws_b, slug=slug_b)

    r_a = await async_client.get("/api/v1/skills/hub", headers=headers_a)
    items_a = {it["id"] for it in r_a.json()["items"]}
    assert pack_a in items_a
    assert pack_b not in items_a

    r_b = await async_client.get("/api/v1/skills/hub", headers=headers_b)
    items_b = {it["id"] for it in r_b.json()["items"]}
    assert pack_b in items_b
    assert pack_a not in items_b


# ── 2. GET /skills/hub/{id} — happy + cross-tenant 404 ──────
async def test_get_hub_pack_detail_visible(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"d-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)

    r = await async_client.get(f"/api/v1/skills/hub/{pack_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == slug


async def test_get_hub_pack_detail_cross_tenant_returns_404(async_client):
    headers_owner, ws_owner, _ = await _bootstrap(async_client)
    headers_other, _ws_other, _ = await _bootstrap(async_client)
    slug = f"hidden-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_owner, slug=slug)

    r = await async_client.get(f"/api/v1/skills/hub/{pack_id}", headers=headers_other)
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "hub.pack_not_found"


# ── 3. GET /skills/hub/{id}/versions ────────────────────────
async def test_list_versions_for_visible_pack(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"v-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)
    await _seed_hub_version(hub_pack_id=pack_id, version_no=1)

    r = await async_client.get(f"/api/v1/skills/hub/{pack_id}/versions", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub_pack_id"] == pack_id
    assert any(it["version_no"] == 1 and it["is_active"] for it in body["items"])


async def test_list_versions_blocked_for_other_tenant(async_client):
    headers_owner, ws_owner, _ = await _bootstrap(async_client)
    headers_other, _ws_other, _ = await _bootstrap(async_client)
    slug = f"v-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_owner, slug=slug)
    await _seed_hub_version(hub_pack_id=pack_id, version_no=1)

    r = await async_client.get(f"/api/v1/skills/hub/{pack_id}/versions", headers=headers_other)
    assert r.status_code == 404


# ── 4. GET /skills/hub/{id}/versions/active ────────────────
async def test_get_active_version_returns_content(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"a-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)
    await _seed_hub_version(hub_pack_id=pack_id, version_no=1)

    r = await async_client.get(
        f"/api/v1/skills/hub/{pack_id}/versions/active",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_active"] is True
    assert body["content_md"]


async def test_get_active_version_404_when_no_versions(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"empty-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)

    r = await async_client.get(
        f"/api/v1/skills/hub/{pack_id}/versions/active",
        headers=headers,
    )
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "hub.version_not_found"


# ── 5. POST /admin/skills/hub/{id}/transition ───────────────
async def test_admin_transition_tenant_pack_by_workspace_admin(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"tr-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)

    r = await async_client.post(
        f"/api/v1/admin/skills/hub/{pack_id}/transition",
        headers=headers,
        json={"target_state": "deprecated", "reason": "v2 superseded"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "deprecated"


async def test_admin_transition_platform_pack_requires_platform_admin(
    async_client,
):
    headers_user, _ws, _ = await _bootstrap(async_client)
    slug = f"plat-tr-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="platform", tenant_id=None, slug=slug)

    r = await async_client.post(
        f"/api/v1/admin/skills/hub/{pack_id}/transition",
        headers=headers_user,
        json={"target_state": "deprecated", "reason": "trying"},
    )
    assert r.status_code == 403
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "hub.scope_permission_denied"


async def test_admin_transition_blocks_cross_tenant_workspace_admin(
    async_client,
):
    """A workspace admin cannot transition a TENANT-scope pack that
    belongs to a different tenant."""
    headers_owner, ws_owner, _ = await _bootstrap(async_client)
    headers_other, _ws_other, _ = await _bootstrap(async_client)
    slug = f"x-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_owner, slug=slug)

    r = await async_client.post(
        f"/api/v1/admin/skills/hub/{pack_id}/transition",
        headers=headers_other,
        json={"target_state": "deprecated", "reason": "rogue"},
    )
    assert r.status_code == 403
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "hub.scope_permission_denied"


async def test_admin_transition_invalid_edge_returns_409(async_client):
    headers, ws_id, _ = await _bootstrap(async_client)
    slug = f"badedge-{uuid.uuid4().hex[:6]}"
    pack_id = await _seed_hub_pack(scope="tenant", tenant_id=ws_id, slug=slug)

    # ACTIVE → TOMBSTONE is not in ALLOWED_HUB_TRANSITIONS (must go
    # through ARCHIVED first).
    r = await async_client.post(
        f"/api/v1/admin/skills/hub/{pack_id}/transition",
        headers=headers,
        json={"target_state": "tombstone", "reason": "skip the line"},
    )
    assert r.status_code == 409
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "hub.invalid_transition"


async def test_unauthenticated_request_blocked(async_client):
    r = await async_client.get("/api/v1/skills/hub")
    assert r.status_code == 401
