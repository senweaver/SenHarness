"""D9 — verify audit log + marketplace moderation flow end-to-end."""

from __future__ import annotations

import asyncio

import httpx

from app.main import app


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 1) Login (records auth.login audit).
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

        # 2) Login with bad password → should log auth.login_failed.
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "wrong"},
        )
        assert r.status_code == 401, r.text

        # 3) Create a throwaway agent → triggers agent.create.
        r = await c.post(
            "/api/v1/agents",
            headers=H,
            json={
                "name": "[D9] audit-probe",
                "description": "ephemeral",
                "visibility": "workspace",
            },
        )
        assert r.status_code == 201, r.text
        aid = r.json()["id"]

        # 4) Make it public (visibility_change).
        r = await c.patch(
            f"/api/v1/agents/{aid}", headers=H, json={"visibility": "public"}
        )
        assert r.status_code == 200, r.text

        # 5) Report the public agent.
        r = await c.post(
            f"/api/v1/agents/{aid}/report",
            headers=H,
            json={"reason": "spam", "detail": "D9 test"},
        )
        assert r.status_code == 201, r.text
        report = r.json()
        print(f"filed report {report['id']} reason={report['reason']}")

        # 6) Moderation list — caller is owner of this workspace so should see
        #    reports for agents in this workspace.
        r = await c.get("/api/v1/moderation/reports?status=pending", headers=H)
        assert r.status_code == 200, r.text
        queue = r.json()
        print(f"moderation queue = {len(queue)} pending report(s)")
        assert any(q["id"] == report["id"] for q in queue), (
            "report missing from moderation queue"
        )

        # 7) Decide the report → removed. Visibility should flip back to private.
        r = await c.patch(
            f"/api/v1/moderation/reports/{report['id']}",
            headers=H,
            json={"decision": "removed", "note": "D9 test removal"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "removed"

        r = await c.get(f"/api/v1/agents/{aid}", headers=H)
        assert r.status_code == 200 and r.json()["visibility"] == "private", (
            f"agent visibility should be private after removed decision; got {r.json()['visibility']}"
        )
        print("agent visibility flipped to private ✓")

        # 8) Audit events list (workspace scope).
        r = await c.get("/api/v1/audit/events?limit=50", headers=H)
        assert r.status_code == 200, r.text
        events = r.json()
        print(f"audit events returned = {len(events)}")
        actions = {e["action"] for e in events}
        expected = {
            "auth.login",
            "agent.create",
            "agent.visibility_change",
            "agent.report",
            "report.decide",
        }
        missing = expected - actions
        assert not missing, f"missing audit actions: {missing}"
        print(f"audit actions present: {sorted(expected)} ✓")

        # 9) Search filter works.
        r = await c.get(
            "/api/v1/audit/events?action=report.decide", headers=H
        )
        assert r.status_code == 200
        assert all(e["action"] == "report.decide" for e in r.json())
        print("action filter ✓")

        # 10) CSV export returns text/csv.
        r = await c.get("/api/v1/audit/events.csv?limit=10", headers=H)
        assert r.status_code == 200, r.text
        assert "text/csv" in r.headers["content-type"], r.headers["content-type"]
        # First line should be the header.
        first = r.text.lstrip("\ufeff").splitlines()[0]
        assert first.startswith("created_at,action,"), first
        print(f"csv export ok ({len(r.text)} bytes)")

        # 11) Cleanup.
        await c.delete(f"/api/v1/agents/{aid}", headers=H)

        print("\n[PASS] D9 audit + moderation round-trip")


if __name__ == "__main__":
    asyncio.run(main())
