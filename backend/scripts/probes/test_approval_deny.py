"""Verify that denying an approval actually blocks the tool call."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logging.basicConfig(level=logging.WARNING)

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.native.runner import NativeBackend


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[skip] DEEPSEEK_API_KEY not set")
        return
    ws_id, sess_id = uuid.uuid4(), uuid.uuid4()
    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=ws_id,
        session_id=sess_id,
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        user_text="Call `execute` with command `echo DENIED-SHOULD-NOT-RUN`.",
        policy={
            "autonomy_level": "l3",
            "sandbox": {"kind": "docker", "image": "python:3.12-slim"},
            "approvals": True,
            "approval_ttl_seconds": 15,
            "session_id": str(sess_id),
            "workspace_id": str(ws_id),
            "persona_md": "Sandbox has `execute`. Call it.",
        },
    )
    backend = NativeBackend()
    tool_results: list[str] = []
    errors: list[dict] = []

    async def run():
        async for ev in backend.run(req):
            if ev.kind == RunEventKind.TOOL_RESULT:
                r = str(ev.data.get("result") or "")[:200]
                print(f"  [tool_result] {r}")
                tool_results.append(r)
            elif ev.kind == RunEventKind.ERROR:
                errors.append(ev.data)
            elif ev.kind == RunEventKind.FINAL:
                print("  [final]")

    task = asyncio.create_task(run())
    # wait for pending approval, then deny
    for _ in range(30):
        await asyncio.sleep(0.5)
        pending = APPROVAL_MANAGER.peek_pending()
        if pending:
            print(f">>> denying approval for: {pending[0].tool_name} / {pending[0].summary}")
            await APPROVAL_MANAGER.decide(pending[0].id, approved=False, reason="smoke deny")
            break
    await task

    ran = any("DENIED-SHOULD-NOT-RUN" in r for r in tool_results)
    blocked = any("User denied" in r or "blocked" in r.lower() for r in tool_results)
    print(f"\n--- summary: ran={ran} blocked_surfaced={blocked} errors={len(errors)}")


if __name__ == "__main__":
    asyncio.run(main())
