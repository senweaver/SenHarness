"""D8 — smoke test Squad CRUD + Marketplace discover/clone."""
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
        access = r.json()["access_token"]
        me = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"})
        ws_id = me.json()["current_workspace_id"]
        H = {"Authorization": f"Bearer {access}", "X-Workspace-Id": ws_id}
        print(f"workspace = {ws_id}")

        # 1) List agents (need ≥1 to create a squad).
        agents = (await c.get("/api/v1/agents", headers=H)).json()
        assert agents, "no agents in workspace; seed some first"
        a1, a2 = (agents + agents)[:2]
        print(f"have {len(agents)} agents; will use {a1['name']!r} + {a2['name']!r}")

        # 2) Create a squad.
        body = {
            "name": "[D8] Test router squad",
            "description": "ephemeral",
            "strategy": "router",
            "members": [
                {"agent_id": a1["id"], "role_in_squad": "leader", "weight": 0},
                {"agent_id": a2["id"], "role_in_squad": "helper", "weight": 1},
            ],
        }
        r = await c.post("/api/v1/squads", headers=H, json=body)
        assert r.status_code == 201, r.text
        squad = r.json()
        sid = squad["id"]
        print(f"created squad {sid} with {len(squad['members'])} members")

        # 3) Fetch detail.
        r = await c.get(f"/api/v1/squads/{sid}", headers=H)
        assert r.status_code == 200
        assert len(r.json()["members"]) == 2
        print("detail OK")

        # 4) Patch name + replace members with 1 agent.
        r = await c.patch(
            f"/api/v1/squads/{sid}",
            headers=H,
            json={"name": "[D8] Renamed squad"},
        )
        assert r.status_code == 200 and r.json()["name"] == "[D8] Renamed squad"

        r = await c.put(
            f"/api/v1/squads/{sid}/members",
            headers=H,
            json=[{"agent_id": a1["id"], "role_in_squad": "solo", "weight": 0}],
        )
        assert r.status_code == 200 and len(r.json()) == 1
        print("update + replace members OK")

        # 5) Discover marketplace.
        # Make a1 public first so discover has something to find.
        r = await c.patch(
            f"/api/v1/agents/{a1['id']}",
            headers=H,
            json={"visibility": "public"},
        )
        assert r.status_code == 200

        r = await c.get("/api/v1/agents/discover", headers=H)
        assert r.status_code == 200, r.text
        discovered = r.json()
        print(f"discover returned {len(discovered)} public agents")
        assert any(a["id"] == a1["id"] for a in discovered), "a1 missing from discover"

        # 6) Clone it back into the same workspace (allowed since it's public).
        r = await c.post(
            f"/api/v1/agents/{a1['id']}/clone",
            headers=H,
            json={"name": "[D8] Cloned"},
        )
        assert r.status_code == 201, r.text
        cloned = r.json()
        print(f"cloned → id={cloned['id']} name={cloned['name']!r}")
        assert cloned["name"] == "[D8] Cloned"
        assert cloned["visibility"] == "workspace", "clone should not keep public"
        assert cloned["id"] != a1["id"]

        # 7) Cleanup.
        await c.delete(f"/api/v1/squads/{sid}", headers=H)
        await c.delete(f"/api/v1/agents/{cloned['id']}", headers=H)
        await c.patch(
            f"/api/v1/agents/{a1['id']}",
            headers=H,
            json={"visibility": "workspace"},
        )

        # 8) Search query works.
        r = await c.get(
            "/api/v1/agents/discover?q=nonsensetoken123", headers=H
        )
        assert r.status_code == 200
        assert len(r.json()) == 0, "search filter not applied"
        print("search filter OK")

        print("\n[PASS] D8 squad + marketplace round-trip")


if __name__ == "__main__":
    asyncio.run(main())
