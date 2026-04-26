"""D7 — smoke test /api/v1/metrics/usage end-to-end.

Requires the demo account seeded by ``cli seed`` (demo@senharness.app /
senharness). Uses httpx over ASGITransport so we don't need the container to
be networked; it calls `app.main:app` directly.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.main import app


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 1) Login
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]

        me = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"})
        ws_id = me.json().get("current_workspace_id")
        print(f"workspace = {ws_id}")

        headers = {
            "Authorization": f"Bearer {access}",
            "X-Workspace-Id": ws_id or "",
        }

        # 2) Fetch usage report (auto scope)
        r = await c.get("/api/v1/metrics/usage", headers=headers)
        assert r.status_code == 200, r.text
        report = r.json()
        print(f"scope = {report['scope']}")
        print(f"since = {report['since']}   until = {report['until']}")
        s = report["summary"]
        print(
            f"summary: input={s['input_tokens']}  output={s['output_tokens']}  "
            f"cost=${s['cost_usd']:.6f}  turns={s['turns']}  "
            f"sessions={s['sessions']}  avg_latency={s['avg_latency_ms']:.0f}ms"
        )
        print(f"daily buckets: {len(report['daily'])}")
        print(f"top_agents:    {len(report['top_agents'])}")
        print(f"top_models:    {len(report['top_models'])}")

        # 3) Force scope=me
        r = await c.get("/api/v1/metrics/usage?scope=me", headers=headers)
        assert r.status_code == 200, r.text
        print(f"scope=me → {r.json()['scope']}")

        # 4) Window filter (last 7 days)
        r = await c.get(
            "/api/v1/metrics/usage?since=2020-01-01&until=2020-01-02",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        empty = r.json()
        assert empty["summary"]["turns"] == 0
        print(f"empty-window turns: {empty['summary']['turns']} (expected 0) ✓")

        print("\n[PASS] /metrics/usage round-trip")


if __name__ == "__main__":
    asyncio.run(main())
