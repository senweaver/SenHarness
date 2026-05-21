"""D2 E2E — exercise the real runner with sandbox=docker via pydantic-ai Agent."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO)

from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.native.runner import NativeBackend


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[skip] DEEPSEEK_API_KEY not set — LLM run disabled.")
        return

    ws_id = uuid.uuid4()
    sess_id = uuid.uuid4()
    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=ws_id,
        session_id=sess_id,
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        user_text=(
            "Write a Python program that prints the 10th Fibonacci number, "
            "save it as fib.py, run it with `execute`, and report the number."
        ),
        policy={
            "autonomy_level": "l3",
            "sandbox": {"kind": "docker", "image": "python:3.12-slim"},
            "session_id": str(sess_id),
            "workspace_id": str(ws_id),
            "persona_md": (
                "You are a developer assistant. You have a Linux sandbox with "
                "python:3.12 and tools `write_file`, `read_file`, `execute`, "
                "`ls`, `grep`, `glob`, `edit_file`. Use `execute` to run shell commands."
            ),
        },
    )

    tool_calls: list[str] = []
    backend = NativeBackend()
    async for ev in backend.run(req):
        data = ev.data
        if ev.kind == RunEventKind.DELTA:
            import sys

            sys.stdout.write(str(data.get("text", "")))
            sys.stdout.flush()
        elif ev.kind == RunEventKind.TOOL_CALL:
            tool_calls.append(f"{data.get('name')}({str(data.get('args'))[:120]})")
            print(f"  [tool_call] {tool_calls[-1]}")
        elif ev.kind == RunEventKind.TOOL_RESULT:
            out = str(data.get("result") or data.get("output", ""))[:300]
            print(f"  [tool_result] {out}")
        elif ev.kind == RunEventKind.FINAL:
            print(f"\n[final] summary={data.get('summary')}")
        elif ev.kind == RunEventKind.ERROR:
            print(f"[error] {data}")

    print(f"\n--- summary: {len(tool_calls)} tool calls ---")


if __name__ == "__main__":
    asyncio.run(main())
