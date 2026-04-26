"""D12 — verify profile / password / members enrichment / departments tree."""

from __future__ import annotations

import asyncio

import httpx

from app.main import app


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Login
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]
        me = (await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {access}"}
        )).json()
        ws_id = me["current_workspace_id"]
        H = {"Authorization": f"Bearer {access}", "X-Workspace-Id": ws_id}
        original_name = me["name"]
        print(f"workspace={ws_id} me={me['email']}")

        # ── 1) Profile update ──
        r = await c.patch(
            "/api/v1/me",
            headers=H,
            json={"name": "Demo (renamed D12)"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Demo (renamed D12)"
        # Revert so the test is idempotent.
        await c.patch("/api/v1/me", headers=H, json={"name": original_name})
        print("profile name update ✓")

        # ── 2) Password change (with wrong old → 401; revert after) ──
        r = await c.post(
            "/api/v1/me/password",
            headers=H,
            json={"old_password": "wrong", "new_password": "new-password-12345"},
        )
        assert r.status_code == 401, r.text
        print("password change rejects wrong old pw ✓")

        # Actually change and revert — this confirms the happy path works.
        r = await c.post(
            "/api/v1/me/password",
            headers=H,
            json={"old_password": "senharness", "new_password": "senharness2"},
        )
        assert r.status_code == 204, r.text
        # Revert
        r = await c.post(
            "/api/v1/me/password",
            headers=H,
            json={"old_password": "senharness2", "new_password": "senharness"},
        )
        assert r.status_code == 204
        print("password change happy path ✓")

        # ── 3) Members endpoint now returns identity fields ──
        r = await c.get(f"/api/v1/workspaces/{ws_id}/members", headers=H)
        assert r.status_code == 200
        members = r.json()
        assert members, "no members"
        m0 = members[0]
        assert "identity_name" in m0
        assert "identity_email" in m0
        assert m0["identity_name"], m0
        print(
            f"members enriched — first member: {m0['identity_name']!r} "
            f"<{m0['identity_email']}>"
        )

        # ── 4) Departments endpoint returns member_count ──
        # Create a department and assign one member to it.
        r = await c.post(
            "/api/v1/departments",
            headers=H,
            json={"name": "[D12] Engineering", "parent_id": None},
        )
        assert r.status_code == 201, r.text
        dept = r.json()
        dept_id = dept["id"]
        assert dept["member_count"] == 0
        print(f"created dept {dept_id}")

        # Move self into it.
        await c.patch(
            f"/api/v1/workspaces/{ws_id}/members/{me['id']}",
            headers=H,
            json={"department_id": dept_id},
        )

        # Re-list — member_count should be 1.
        r = await c.get("/api/v1/departments", headers=H)
        assert r.status_code == 200
        got = next(d for d in r.json() if d["id"] == dept_id)
        assert got["member_count"] == 1, got
        print(f"member_count = {got['member_count']} (expected 1) ✓")

        # Create child department and test tree rename + move.
        r = await c.post(
            "/api/v1/departments",
            headers=H,
            json={"name": "[D12] Frontend", "parent_id": dept_id},
        )
        assert r.status_code == 201
        child = r.json()
        assert child["path"] == "[D12] Engineering/[D12] Frontend"
        print(f"child path = {child['path']} ✓")

        # Rename the child
        r = await c.patch(
            f"/api/v1/departments/{child['id']}",
            headers=H,
            json={"name": "[D12] Web"},
        )
        assert r.status_code == 200
        assert r.json()["path"].endswith("[D12] Web")
        print(f"rename → {r.json()['path']} ✓")

        # Move child to root
        r = await c.patch(
            f"/api/v1/departments/{child['id']}",
            headers=H,
            json={"parent_id": None},
        )
        assert r.status_code == 200, r.text
        moved = r.json()
        assert moved["parent_id"] is None
        assert moved["path"] == "[D12] Web", moved
        print(f"moved to root — path = {moved['path']} ✓")

        # Cycle prevention — try to move parent under its descendant
        # (no longer a descendant since we moved up; re-nest child first)
        await c.patch(
            f"/api/v1/departments/{child['id']}",
            headers=H,
            json={"parent_id": dept_id},
        )
        r = await c.patch(
            f"/api/v1/departments/{dept_id}",
            headers=H,
            json={"parent_id": child["id"]},
        )
        assert r.status_code == 400, r.text
        print("cycle prevention ✓")

        # Cleanup
        await c.patch(
            f"/api/v1/workspaces/{ws_id}/members/{me['id']}",
            headers=H,
            json={"department_id": None},
        )
        await c.delete(f"/api/v1/departments/{child['id']}", headers=H)
        await c.delete(f"/api/v1/departments/{dept_id}", headers=H)

        print("\n[PASS] D12 profile + members + departments")


if __name__ == "__main__":
    asyncio.run(main())
