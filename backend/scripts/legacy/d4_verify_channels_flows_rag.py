"""D4 — smoke test Channels ingress, Flows manual run, RAG ingest + search."""

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
        me = (await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {access}"}
        )).json()
        ws_id = me["current_workspace_id"]
        H = {"Authorization": f"Bearer {access}", "X-Workspace-Id": ws_id}

        agents = (await c.get("/api/v1/agents", headers=H)).json()
        assert agents, "no agents; seed first"
        agent_id = agents[0]["id"]
        print(f"workspace={ws_id} using agent={agents[0]['name']!r}")

        # ═══════════════════════════════════════════════════
        # Part 1 — Channels
        # ═══════════════════════════════════════════════════
        print("\n— Channels —")
        r = await c.post(
            "/api/v1/channels",
            headers=H,
            json={
                "name": "[D4] webhook probe",
                "kind": "webhook",
                "default_agent_id": agent_id,
                "config_json": {},
            },
        )
        assert r.status_code == 201, r.text
        ch = r.json()
        cid = ch["id"]
        token = ch["inbound_token"]
        print(f"created channel {cid} token=***{token[-6:]}")

        # Ingress without token → 403
        r = await c.post(
            f"/api/v1/hooks/ingress/{cid}",
            json={"text": "hello"},
            params={"token": "wrong_token_0000000000"},
        )
        assert r.status_code == 403, r.text
        print("wrong token rejected ✓")

        # Ingress with correct token → queued
        r = await c.post(
            f"/api/v1/hooks/ingress/{cid}",
            json={"text": "smoke test", "thread": "d4-probe", "user": "tester"},
            params={"token": token},
        )
        assert r.status_code == 200, r.text
        ack = r.json()
        assert ack["accepted"] is True
        print(f"accepted; session={ack['session_id']}")

        # Ingress second message in same thread — should reuse session.
        await c.post(
            f"/api/v1/hooks/ingress/{cid}",
            json={"text": "second message", "thread": "d4-probe", "user": "tester"},
            params={"token": token},
        )

        # List channels
        r = await c.get("/api/v1/channels", headers=H)
        assert r.status_code == 200
        assert any(x["id"] == cid for x in r.json())
        # The bot_token masking: write a secret, GET it back masked.
        await c.patch(
            f"/api/v1/channels/{cid}",
            headers=H,
            json={"config_json": {"bot_token": "xoxb-supersecret-123456789"}},
        )
        r = await c.get(f"/api/v1/channels/{cid}", headers=H)
        cfg = r.json()["config_json"]
        assert cfg.get("bot_token", "").startswith("•••"), cfg
        print("bot_token masked on GET ✓")

        # Rotate token
        r = await c.post(f"/api/v1/channels/{cid}/rotate-token", headers=H)
        new_token = r.json()["inbound_token"]
        assert new_token != token
        # Old token fails now
        r = await c.post(
            f"/api/v1/hooks/ingress/{cid}",
            json={"text": "after-rotate"},
            params={"token": token},
        )
        assert r.status_code == 403
        print("old token rejected after rotate ✓")

        # Cleanup channel
        await c.delete(f"/api/v1/channels/{cid}", headers=H)

        # ═══════════════════════════════════════════════════
        # Part 2 — Flows (manual + webhook)
        # ═══════════════════════════════════════════════════
        print("\n— Flows —")
        r = await c.post(
            "/api/v1/flows",
            headers=H,
            json={
                "name": "[D4] test flow",
                "description": "ephemeral",
                "trigger_kind": "webhook",
                "trigger_config": {"token": "d4-hook-secret-12345678"},
                "agent_id": agent_id,
                "prompt_template": "Hello from the {{source}} run.",
            },
        )
        assert r.status_code == 201, r.text
        flow = r.json()
        fid = flow["id"]
        print(f"created flow {fid}")

        # Manual trigger
        r = await c.post(
            f"/api/v1/flows/{fid}/run",
            headers=H,
            json={"payload": {"source": "manual"}},
        )
        assert r.status_code == 202, r.text
        run = r.json()
        print(f"manual run {run['id']} status={run['status']}")

        # Webhook trigger — bad token (must be long enough to pass validation)
        r = await c.post(
            f"/api/v1/hooks/flow/{fid}",
            params={"token": "wrong_token_that_passes_length"},
            json={"source": "webhook"},
        )
        assert r.status_code == 403, r.text
        # Correct token
        r = await c.post(
            f"/api/v1/hooks/flow/{fid}",
            params={"token": "d4-hook-secret-12345678"},
            json={"source": "webhook"},
        )
        assert r.status_code == 200, r.text
        wrun = r.json()
        assert wrun["trigger_kind"] == "webhook"
        print(f"webhook trigger ok; run={wrun['id']}")

        # List runs
        r = await c.get(f"/api/v1/flows/{fid}/runs", headers=H)
        assert r.status_code == 200
        runs = r.json()
        assert len(runs) >= 2
        print(f"flow has {len(runs)} runs in history")

        await c.delete(f"/api/v1/flows/{fid}", headers=H)

        # ═══════════════════════════════════════════════════
        # Part 3 — RAG
        # ═══════════════════════════════════════════════════
        print("\n— Knowledge (RAG) —")
        r = await c.post(
            "/api/v1/knowledge/collections",
            headers=H,
            json={"name": "[D4] smoke kb", "description": "ephemeral"},
        )
        assert r.status_code == 201, r.text
        col = r.json()
        col_id = col["id"]
        print(f"created collection {col_id}")

        # Ingest a text doc
        text = (
            "SenHarness is an agent-operations platform. "
            "It supports scheduled flows, multi-agent squads, and retrieval-augmented knowledge bases. "
            "The RAG system embeds chunks with pgvector and serves them to agents through the knowledge_search tool."
        ) * 3
        r = await c.post(
            f"/api/v1/knowledge/collections/{col_id}/docs",
            headers=H,
            json={
                "title": "D4 overview",
                "source_kind": "text",
                "raw_text": text,
            },
        )
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["status"] == "ready", doc
        assert doc["chunk_count"] > 0
        print(f"ingested doc {doc['id']} chunks={doc['chunk_count']}")

        # Search
        r = await c.post(
            f"/api/v1/knowledge/collections/{col_id}/search",
            headers=H,
            json={"query": "how does the knowledge base work?", "top_k": 3},
        )
        assert r.status_code == 200, r.text
        hits = r.json()
        assert len(hits) > 0
        print(f"search returned {len(hits)} hits; top score={hits[0]['score']:.3f}")
        assert "knowledge_search" in hits[0]["text"] or "RAG" in hits[0]["text"], (
            f"unexpected top hit: {hits[0]['text']!r}"
        )
        print("search returned relevant chunk ✓")

        # List docs
        r = await c.get(
            f"/api/v1/knowledge/collections/{col_id}/docs", headers=H
        )
        assert len(r.json()) == 1

        # Cleanup
        await c.delete(
            f"/api/v1/knowledge/collections/{col_id}", headers=H
        )

        print("\n[PASS] D4 channels + flows + RAG round-trip")


if __name__ == "__main__":
    asyncio.run(main())
