"""D16 — verify admin stats / identities / workspaces endpoints.

The demo account is seeded as PlatformRole.USER; we promote it for the test
then revert.
"""

from __future__ import annotations

import asyncio

import httpx

from app.db.models.identity import PlatformRole
from app.db.session import get_session_factory
from app.main import app
from app.repositories.identity import IdentityRepository


async def _promote(email: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        repo = IdentityRepository(db)
        ident = await repo.get_by_email(email.lower())
        assert ident is not None
        ident.platform_role = PlatformRole.PLATFORM_ADMIN
        await db.flush([ident])
        await db.commit()


async def _demote(email: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        repo = IdentityRepository(db)
        ident = await repo.get_by_email(email.lower())
        assert ident is not None
        ident.platform_role = PlatformRole.USER
        await db.flush([ident])
        await db.commit()


async def main() -> None:
    email = "demo@senharness.app"
    await _promote(email)
    try:
        await _run_tests(email)
    finally:
        await _demote(email)


async def _run_tests(email: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "senharness"},
        )
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]
        H = {"Authorization": f"Bearer {access}"}

        # ── 1) Stats ──
        r = await c.get("/api/v1/admin/stats", headers=H)
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["identities_total"] >= 1
        assert stats["workspaces_total"] >= 1
        assert stats["platform_admins"] >= 1
        print(
            f"stats: users={stats['identities_total']} "
            f"workspaces={stats['workspaces_total']} "
            f"agents={stats['agents_total']} "
            f"audit24h={stats['audit_events_24h']}"
        )

        # ── 2) Identity list + filter + detail ──
        r = await c.get("/api/v1/admin/identities", headers=H)
        assert r.status_code == 200
        idents = r.json()
        me_row = next((x for x in idents if x["email"] == email), None)
        assert me_row is not None, "demo user missing from list"
        assert me_row["platform_role"] == "platform_admin"
        print(f"identities returned: {len(idents)}; first = {me_row['email']!r}")

        r = await c.get(
            "/api/v1/admin/identities?role=platform_admin", headers=H
        )
        assert all(x["platform_role"] == "platform_admin" for x in r.json())
        print("role filter ✓")

        r = await c.get(f"/api/v1/admin/identities/{me_row['id']}", headers=H)
        assert r.status_code == 200
        detail = r.json()
        assert detail["workspace_count"] >= 1
        assert detail["workspaces"], "detail lacks workspace memberships"
        print(
            f"identity detail ok — {detail['workspace_count']} workspace(s): "
            f"{[w['slug'] for w in detail['workspaces']]}"
        )

        # ── 3) Self-demote refused ──
        r = await c.patch(
            f"/api/v1/admin/identities/{me_row['id']}",
            headers=H,
            json={"platform_role": "user"},
        )
        assert r.status_code == 400, r.text
        print("self-demote refused ✓")

        # ── 4) Workspace list + detail + patch + revert ──
        r = await c.get("/api/v1/admin/workspaces", headers=H)
        assert r.status_code == 200
        wss = r.json()
        assert wss
        w0 = wss[0]
        print(
            f"workspaces: {len(wss)} — first={w0['slug']!r} "
            f"members={w0['member_count']} agents={w0['agent_count']}"
        )
        assert w0["member_count"] >= 1

        r = await c.get(f"/api/v1/admin/workspaces/{w0['id']}", headers=H)
        assert r.status_code == 200
        original_plan = r.json()["plan"]

        # Patch plan to 'team', then revert.
        r = await c.patch(
            f"/api/v1/admin/workspaces/{w0['id']}",
            headers=H,
            json={"plan": "team"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["plan"] == "team"
        print("plan patch → team ✓")
        await c.patch(
            f"/api/v1/admin/workspaces/{w0['id']}",
            headers=H,
            json={"plan": original_plan},
        )

        # ── 5) Non-admin blocked ──
        # Revert to user role, try again — should 403.
        await _demote(email)
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "senharness"},
        )
        user_access = r.json()["access_token"]
        H2 = {"Authorization": f"Bearer {user_access}"}
        r = await c.get("/api/v1/admin/stats", headers=H2)
        assert r.status_code == 403, r.text
        print("non-admin blocked (403) ✓")

        # Re-promote for the teardown
        await _promote(email)

        print("\n[PASS] D16 admin round-trip")


if __name__ == "__main__":
    asyncio.run(main())
