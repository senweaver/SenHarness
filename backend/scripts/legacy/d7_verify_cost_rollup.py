"""D7 — verify that NEW-schema token_usage_json (with cost/model) rolls up
correctly via /metrics/usage. Inserts a synthetic assistant message and
checks that cost and top_models populate.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx

from app.core.pricing import calc_cost_usd
from app.db.models.message import Message, MessageRole
from app.db.session import get_session_factory
from app.main import app
from app.repositories.agent import AgentRepository
from app.repositories.session import SessionRepository


async def _seed_synthetic() -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a fake assistant turn with a proper usage blob. Returns
    (workspace_id, message_id)."""
    factory = get_session_factory()
    async with factory() as db:
        # Find some session/agent belonging to the demo user.
        any_session = (await db.execute(
            __import__("sqlalchemy").select(
                __import__("app.db.models.session", fromlist=["Session"]).Session
            ).limit(1)
        )).scalars().first()
        assert any_session is not None, "no sessions in DB — run a chat first"

        # Grab one agent in the same workspace.
        agents = await AgentRepository(db).list_visible(
            workspace_id=any_session.workspace_id,
            identity_id=any_session.owner_identity_id,
            limit=1,
        )
        agent_id = agents[0].id if agents else None

        # Compute a realistic cost against a known-catalog model.
        inp, out = 1000, 500
        cost = calc_cost_usd("gpt-4o", "openai", inp, out)
        msg = Message(
            workspace_id=any_session.workspace_id,
            session_id=any_session.id,
            role=MessageRole.ASSISTANT,
            author_agent_id=agent_id,
            content_json={"text": "[D7 synthetic]"},
            token_usage_json={
                "input": inp,
                "output": out,
                "cost": cost["cost"],
                "cost_currency": "USD",
                "cost_matched_model": cost["matched_model"],
                "latency_ms": 1234,
                "provider": "openai",
                "model": "gpt-4o",
            },
        )
        db.add(msg)
        await db.commit()
        print(
            f"seeded synthetic message: cost=${cost['cost']:.6f} "
            f"matched={cost['matched_model']}"
        )
        return any_session.workspace_id, msg.id


async def main() -> None:
    ws_id, msg_id = await _seed_synthetic()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        access = r.json()["access_token"]
        headers = {
            "Authorization": f"Bearer {access}",
            "X-Workspace-Id": str(ws_id),
        }

        r = await c.get(
            "/api/v1/metrics/usage?scope=workspace",
            headers=headers,
        )
        assert r.status_code == 200, r.text
        report = r.json()
        s = report["summary"]
        print(
            f"summary: cost=${s['cost_usd']:.6f}  latency_ms≈{s['avg_latency_ms']:.0f}"
        )
        assert s["cost_usd"] > 0, "cost rollup returned 0 — synthetic insert failed"

        by_model = [m for m in report["top_models"] if m["model"] == "gpt-4o"]
        assert by_model, f"gpt-4o row missing; got {report['top_models']}"
        print(f"gpt-4o bucket: cost=${by_model[0]['cost_usd']:.6f}")

        print("\n[PASS] cost/model rollup works end-to-end")

    # Clean up so repeated runs don't skew metrics forever.
    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(Message, msg_id)
        if row is not None:
            await db.delete(row)
            await db.commit()
            print("cleaned up synthetic message")


if __name__ == "__main__":
    asyncio.run(main())
