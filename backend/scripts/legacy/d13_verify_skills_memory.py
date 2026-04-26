"""D13 — smoke test Skills upload/detail/delete + Memory stats/search/recall."""

from __future__ import annotations

import asyncio

import httpx

from app.main import app


SAMPLE_SKILL = """---
name: d13-probe
description: D13 ephemeral skill for smoke test.
license: MIT
---

# 测试技能

这是一段写给 Agent 看的指令。

## 何时启用

- 当用户问起 D13 测试时。

## 风格

- 简短，直接。
"""


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

        # ── Part 1 — Skills ──
        print("— Skills —")
        r = await c.get("/api/v1/skills", headers=H)
        assert r.status_code == 200, r.text
        before = r.json()
        bundled = [s for s in before if s["source"] == "bundled"]
        print(f"bundled = {len(bundled)} (found: {[s['slug'] for s in bundled]})")
        assert bundled, "no bundled skills found in image"
        assert bundled[0]["prompt_preview"], bundled[0]

        # Fetch detail for one bundled skill
        b0 = bundled[0]
        r = await c.get(
            f"/api/v1/skills/bundled/{b0['slug']}", headers=H
        )
        assert r.status_code == 200, r.text
        detail = r.json()
        assert "content" in detail and len(detail["content"]) > 0
        assert "---" in detail["content"]
        print(
            f"bundled detail: name={detail['name']!r} body_length={detail['body_length']}"
        )

        # Upload a workspace skill
        r = await c.post(
            "/api/v1/skills",
            headers=H,
            json={"slug": "d13-probe", "content": SAMPLE_SKILL},
        )
        assert r.status_code == 201, r.text
        uploaded = r.json()
        assert uploaded["source"] == "workspace"
        assert uploaded["description"].startswith("D13")
        print(f"uploaded: slug={uploaded['slug']!r}")

        # Bad slug rejected
        r = await c.post(
            "/api/v1/skills",
            headers=H,
            json={"slug": "UPPER CASE", "content": "x"},
        )
        assert r.status_code == 400, r.text
        print("invalid slug rejected ✓")

        # List again — should now include workspace skill
        r = await c.get("/api/v1/skills", headers=H)
        assert any(
            s["source"] == "workspace" and s["slug"] == "d13-probe"
            for s in r.json()
        )
        print("workspace skill shows in list ✓")

        # Cannot delete bundled (there's no endpoint, only workspace)
        r = await c.delete(
            f"/api/v1/skills/workspace/{'nonexistent-skill'}", headers=H
        )
        assert r.status_code == 404, r.text

        # Delete workspace skill
        r = await c.delete("/api/v1/skills/workspace/d13-probe", headers=H)
        assert r.status_code == 204, r.text
        print("workspace skill deleted ✓")

        # ── Part 2 — Memory ──
        print("\n— Memory —")

        # Create 3 memories
        base = {
            "scope": "user",
            "kind": "semantic",
            "content": "",
        }
        for text in [
            "The user prefers vim and tmux on macOS.",
            "Weekly standup is Monday at 10am Shanghai time.",
            "Project X uses FastAPI with async SQLAlchemy 2.",
        ]:
            r = await c.post(
                "/api/v1/memory", headers=H, json={**base, "content": text}
            )
            assert r.status_code == 201, r.text

        # Stats
        r = await c.get("/api/v1/memory/stats", headers=H)
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["total"] >= 3, stats
        assert stats["by_scope"].get("user", 0) >= 3
        assert stats["by_kind"].get("semantic", 0) >= 3
        print(
            f"stats: total={stats['total']} "
            f"by_scope={stats['by_scope']} by_kind={stats['by_kind']}"
        )

        # Text search
        r = await c.get(
            "/api/v1/memory?q=standup&scope=user", headers=H
        )
        assert r.status_code == 200
        hits = r.json()
        assert any("standup" in m["content"] for m in hits), hits
        print(f"text search returned {len(hits)} hits ✓")

        # Semantic recall
        r = await c.post(
            "/api/v1/memory/recall",
            headers=H,
            json={"query": "what editor does the user like?", "limit": 3, "min_score": 0.1},
        )
        assert r.status_code == 200, r.text
        recall_hits = r.json()
        print(f"recall returned {len(recall_hits)} hits")
        if recall_hits:
            top = recall_hits[0]
            print(
                f"top hit: score={top['score']:.3f}  text={top['memory']['content'][:60]!r}"
            )
            assert top["score"] > 0

        # Update a memory
        if hits:
            mid = hits[0]["id"]
            r = await c.patch(
                f"/api/v1/memory/{mid}",
                headers=H,
                json={"confidence": 0.8},
            )
            assert r.status_code == 200, r.text
            assert r.json()["confidence"] == 0.8
            print("memory update ✓")

        # Cleanup
        r = await c.get("/api/v1/memory?q=standup", headers=H)
        for m in r.json():
            await c.delete(f"/api/v1/memory/{m['id']}", headers=H)
        r = await c.get("/api/v1/memory?q=vim", headers=H)
        for m in r.json():
            await c.delete(f"/api/v1/memory/{m['id']}", headers=H)
        r = await c.get("/api/v1/memory?q=FastAPI", headers=H)
        for m in r.json():
            await c.delete(f"/api/v1/memory/{m['id']}", headers=H)

        print("\n[PASS] D13 skills + memory round-trip")


if __name__ == "__main__":
    asyncio.run(main())
