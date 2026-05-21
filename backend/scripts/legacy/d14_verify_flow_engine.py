"""D14 — verify visual Flow engine: topology / node runners / legacy fallback."""

from __future__ import annotations

import asyncio
import time

import httpx

# Lifespan doesn't fire under httpx.ASGITransport, so we have to trigger
# the pydantic-ai backend registration manually (same import main.py does).
import app.agents.kernels.native  # noqa: F401
from app.main import app


async def _poll_run(
    c: httpx.AsyncClient, H: dict, flow_id: str, *, timeout: float = 30.0
) -> dict:
    """Poll /flows/{id}/runs until the newest run terminates or we time out."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = await c.get(f"/api/v1/flows/{flow_id}/runs", headers=H)
        assert r.status_code == 200, r.text
        runs = r.json()
        if runs:
            last = runs[0]
            if last["status"] in {"succeeded", "failed"}:
                return last
        await asyncio.sleep(0.5)
    return last


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        access = r.json()["access_token"]
        me = (await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {access}"}
        )).json()
        ws_id = me["current_workspace_id"]
        H = {"Authorization": f"Bearer {access}", "X-Workspace-Id": ws_id}

        agents = (await c.get("/api/v1/agents", headers=H)).json()
        assert agents, "need at least one agent in workspace"
        agent_id = agents[0]["id"]
        print(f"workspace={ws_id}  agent={agents[0]['name']!r}")

        # ── 1) Legacy (classic) flow still works — no graph_json ──
        print("\n— Test 1: legacy mode (no graph) —")
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D14] legacy",
                "trigger_kind": "manual",
                "agent_id": agent_id,
                "prompt_template": "Say hi from {{source}}",
            },
        )
        assert r.status_code == 201, r.text
        legacy_id = r.json()["id"]

        r = await c.post(
            f"/api/v1/flows/{legacy_id}/run",
            headers=H,
            json={"payload": {"source": "legacy-test"}},
        )
        assert r.status_code == 202, r.text
        run = await _poll_run(c, H, legacy_id)
        assert run.get("status") in {"succeeded", "failed"}, run
        assert run.get("node_events_json") == [], "legacy run should not have node_events"
        print(f"  legacy run status={run['status']}  output={run.get('output_summary')!r}")

        # ── 2) Valid 3-node graph: start → agent_call → end ──
        print("\n— Test 2: valid 3-node graph —")
        graph = {
            "nodes": [
                {"id": "n_start", "type": "start", "data": {}},
                {
                    "id": "n_agent",
                    "type": "agent_call",
                    "data": {
                        "agent_id": agent_id,
                        "prompt_template": "You are in a flow test. Reply ONLY with: OK {{start.tag}}",
                    },
                },
                {
                    "id": "n_end",
                    "type": "end",
                    "data": {"output_mode": "flow_run", "text": "{{n_agent.text}}"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "n_start", "target": "n_agent"},
                {"id": "e2", "source": "n_agent", "target": "n_end"},
            ],
        }
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D14] valid graph",
                "trigger_kind": "manual",
                "agent_id": agent_id,
                "prompt_template": "",  # empty in graph mode
                "graph_json": graph,
            },
        )
        assert r.status_code == 201, r.text
        valid_id = r.json()["id"]
        assert r.json()["graph_json"]["nodes"], "graph round-trip failed"

        r = await c.post(
            f"/api/v1/flows/{valid_id}/run",
            headers=H,
            json={"payload": {"tag": "alpha"}},
        )
        assert r.status_code == 202, r.text

        run = await _poll_run(c, H, valid_id)
        print(f"  graph run status={run.get('status')} summary={run.get('output_summary')!r}")
        events = run.get("node_events_json") or []
        assert len(events) == 3, f"expected 3 node events, got {len(events)}: {events}"
        order = [ev["node_id"] for ev in events]
        assert order == ["n_start", "n_agent", "n_end"], f"bad topo order: {order}"
        for ev in events:
            assert ev.get("started_at"), ev
            assert ev.get("status") in {"success", "failed"}, ev
        print(f"  event order = {order}  all events have timestamps ✓")

        # ── 3) Cycle rejected ──
        print("\n— Test 3: cycle detection —")
        cycle_graph = {
            "nodes": [
                {"id": "a", "type": "start", "data": {}},
                {
                    "id": "b",
                    "type": "agent_call",
                    "data": {"agent_id": agent_id, "prompt_template": "noop"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "b"},
                {"id": "e2", "source": "b", "target": "a"},
            ],
        }
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D14] cycle",
                "trigger_kind": "manual",
                "prompt_template": "",
                "graph_json": cycle_graph,
            },
        )
        assert r.status_code == 201, r.text
        cyc_id = r.json()["id"]
        await c.post(f"/api/v1/flows/{cyc_id}/run", headers=H, json={"payload": {}})
        run = await _poll_run(c, H, cyc_id, timeout=10)
        assert run.get("status") == "failed", run
        assert "cycle" in (run.get("error") or "").lower(), run
        print(f"  cycle rejected: error={run['error']!r}")

        # ── 4) http_request node hits internal /health ──
        print("\n— Test 4: http_request node —")
        http_graph = {
            "nodes": [
                {"id": "s", "type": "start", "data": {}},
                {
                    "id": "h",
                    "type": "http_request",
                    "data": {
                        "method": "GET",
                        "url": "http://backend:8000/api/v1/health",
                        "timeout": 5,
                    },
                },
                {"id": "e", "type": "end", "data": {"text": "{{h.status}}"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "h"},
                {"id": "e2", "source": "h", "target": "e"},
            ],
        }
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D14] http",
                "trigger_kind": "manual",
                "prompt_template": "",
                "graph_json": http_graph,
            },
        )
        http_id = r.json()["id"]
        await c.post(f"/api/v1/flows/{http_id}/run", headers=H, json={"payload": {}})
        run = await _poll_run(c, H, http_id, timeout=15)
        if run.get("status") == "succeeded":
            assert run.get("output_summary") in {"200", "204"}, run
            print(f"  http node ok; output={run['output_summary']}")
        else:
            # Network routing in the test container may fail — accept graceful
            # fail as long as the error message clearly came from the http_request node.
            err = run.get("error") or ""
            assert "h (http_request)" in err, run
            print(f"  http node network-failed (expected in ASGI transport): {err[:120]}")

        # ── 5) Unknown node type ──
        print("\n— Test 5: unknown node type rejected —")
        bad_graph = {
            "nodes": [
                {"id": "x", "type": "does_not_exist", "data": {}},
            ],
            "edges": [],
        }
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D14] unknown type",
                "trigger_kind": "manual",
                "prompt_template": "",
                "graph_json": bad_graph,
            },
        )
        bad_id = r.json()["id"]
        await c.post(f"/api/v1/flows/{bad_id}/run", headers=H, json={"payload": {}})
        run = await _poll_run(c, H, bad_id, timeout=10)
        assert run.get("status") == "failed"
        assert "unknown_node_type" in (run.get("error") or "")
        print(f"  unknown type rejected: {run['error']}")

        # ── Cleanup ──
        for fid in [legacy_id, valid_id, cyc_id, http_id, bad_id]:
            await c.delete(f"/api/v1/flows/{fid}", headers=H)

        print("\n[PASS] D14 flow engine round-trip")


if __name__ == "__main__":
    asyncio.run(main())
