"""D3 E2E — verify HITL approval flow end-to-end.

Scenario:
  - Build an agent policy with sandbox=docker + approvals=True.
  - Kick the runner in a background task with a prompt that will trigger
    `execute` inside the sandbox.
  - Poll ``APPROVAL_MANAGER.peek_pending()`` until an approval appears.
  - Call ``APPROVAL_MANAGER.decide(approved=True)``; the run should resume
    and complete.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO)

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.native.runner import NativeBackend


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[skip] DEEPSEEK_API_KEY not set")
        return

    ws_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=ws_id,
        session_id=sess_id,
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        user_text="Run the shell command `echo HITL-OK` in the sandbox and report the output.",
        policy={
            "autonomy_level": "l3",
            "sandbox": {"kind": "docker", "image": "python:3.12-slim"},
            "approvals": True,
            "approval_ttl_seconds": 20,
            "session_id": str(sess_id),
            "workspace_id": str(ws_id),
            "persona_md": "You have a Linux sandbox with `execute`. Use it.",
        },
    )

    backend = NativeBackend()
    events: list[tuple[str, dict]] = []

    async def run():
        async for ev in backend.run(req):
            events.append((ev.kind.value, ev.data))
            if ev.kind == RunEventKind.TOOL_CALL:
                print(f"  [tool_call] {ev.data.get('name')}({str(ev.data.get('args'))[:80]})")
            elif ev.kind == RunEventKind.TOOL_RESULT:
                r = str(ev.data.get("result", ""))[:120]
                print(f"  [tool_result] {r}")
            elif ev.kind == RunEventKind.FINAL:
                print(f"  [final]")
            elif ev.kind == RunEventKind.ERROR:
                print(f"  [error] {ev.data}")

    # Run the agent in the background.
    run_task = asyncio.create_task(run())

    # Poll for a pending approval.
    approval_seen = False
    for attempt in range(40):
        await asyncio.sleep(0.5)
        pending = APPROVAL_MANAGER.peek_pending()
        if pending:
            approval_seen = True
            entry = pending[0]
            print(f"\n>>> pending approval: tool={entry.tool_name} summary={entry.summary!r}")
            print(">>> approving ...")
            await APPROVAL_MANAGER.decide(entry.id, approved=True, reason="smoke test")
            break
    if not approval_seen:
        print("!!! no approval requested — ToolGuard may not have matched execute")
    await run_task

    # Count tool calls and tool_results; verify HITL-OK surfaced.
    tool_calls = sum(1 for k, _ in events if k == "tool_call")
    tool_results = [d for k, d in events if k == "tool_result"]
    hit_ok = any("HITL-OK" in str(d.get("result", "")) for d in tool_results)
    print(f"\n--- summary: {tool_calls} tool_calls, {len(tool_results)} tool_results, HITL-OK={hit_ok}")


if __name__ == "__main__":
    asyncio.run(main())
