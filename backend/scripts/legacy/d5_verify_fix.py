"""D5 fix verification — drive the runner with the same metadata the UI saved.

Reads the actual sandbox agent ('沙箱审批演示 Agent') from the DB and runs a
live turn against it through the real NativeBackend. Confirms that with
the new sessions.py policy propagation, an approval row IS created.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logging.basicConfig(level=logging.WARNING)

from sqlalchemy import select

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.native.runner import NativeBackend
from app.db.models.agent import Agent
from app.db.session import get_session_factory


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[skip] DEEPSEEK_API_KEY not set"); return

    factory = get_session_factory()
    async with factory() as db:
        agent = (
            await db.execute(
                select(Agent).where(Agent.name == "沙箱审批演示 Agent").limit(1)
            )
        ).scalar_one_or_none()
        if agent is None:
            print("[skip] agent '沙箱审批演示 Agent' not found")
            return
        ws_id = agent.workspace_id
        agent_id = agent.id
        md = dict(agent.metadata_json or {})

    print(f"Loaded agent: {agent.name}")
    print(f"  sandbox = {md.get('sandbox')!r}")
    print(f"  approvals = {md.get('approvals')!r}")
    print(f"  autonomy = {agent.autonomy_level}")

    sess_id = uuid.uuid4()
    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=ws_id,
        session_id=sess_id,
        identity_id=uuid.uuid4(),
        agent_id=agent_id,
        user_text="请运行 shell 命令：echo D5-VERIFIED-AFTER-FIX",
        policy={
            "autonomy_level": "l3",
            "code_mode": md.get("code_mode"),
            "context": md.get("context") or {},
            "subagents": md.get("subagents"),
            "skills": md.get("skills"),
            "todos": md.get("todos"),
            "sandbox": md.get("sandbox"),
            "approvals": md.get("approvals"),
            "shields": md.get("shields"),
            "budget": md.get("budget"),
            "approval_ttl_seconds": 30,
            "session_id": str(sess_id),
            "workspace_id": str(ws_id),
            "persona_md": agent.persona_md,
        },
    )

    backend = NativeBackend()
    tool_results: list[str] = []

    async def run():
        async for ev in backend.run(req):
            if ev.kind == RunEventKind.TOOL_CALL:
                print(f"  [tool_call] {ev.data.get('name')}({str(ev.data.get('args'))[:80]})")
            elif ev.kind == RunEventKind.TOOL_RESULT:
                r = str(ev.data.get("result") or "")[:200]
                tool_results.append(r)
                print(f"  [tool_result] {r}")
            elif ev.kind == RunEventKind.FINAL:
                print("  [final]")

    task = asyncio.create_task(run())

    pending_seen = False
    for _ in range(40):
        await asyncio.sleep(0.5)
        pending = APPROVAL_MANAGER.peek_pending()
        if pending:
            pending_seen = True
            entry = pending[0]
            print(f"\n>>> APPROVAL APPEARED: tool={entry.tool_name} summary={entry.summary!r}")
            print(">>> approving via APPROVAL_MANAGER.decide(approved=True)")
            await APPROVAL_MANAGER.decide(entry.id, approved=True, reason="d5 verify")
            break
    if not pending_seen:
        print("\n!!! NO APPROVAL APPEARED — fix did not work")
    await task

    print(f"\n--- result: pending_seen={pending_seen} tool_results={len(tool_results)} ok={any('D5-VERIFIED-AFTER-FIX' in r for r in tool_results)}")


if __name__ == "__main__":
    asyncio.run(main())
