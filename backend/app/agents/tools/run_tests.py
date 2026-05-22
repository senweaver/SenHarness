"""`run_tests` — invoke the agent-configured test command inside the sandbox.

Reads ``policy.coding.test_command`` (e.g. ``pytest -x``) and executes it
through the active ConsoleCapability backend, if one is attached to the run.

When no backend is available (autonomy=L1 or sandbox=None), we return a
structured ``no_sandbox`` response — the agent is expected to surface that
to the user rather than claim tests passed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.agents.harness.sandbox import active_backend_for
from app.agents.tools._context import get_context


class RunTestsArgs(BaseModel):
    command: str | None = Field(
        default=None,
        description=(
            "Optional override for the test command; defaults to `policy.coding.test_command`."
        ),
    )
    timeout_s: int = Field(default=120, ge=5, le=600, description="Hard timeout in seconds.")


async def run_run_tests(args: RunTestsArgs) -> dict[str, Any]:
    ctx = get_context()
    backend = active_backend_for(ctx.run_id)
    if backend is None:
        return {
            "ok": False,
            "reason": "no_sandbox",
            "hint": (
                "This agent is not running inside a sandbox; enable a "
                "`metadata.sandbox` block to use run_tests."
            ),
        }

    coding_block = (ctx.policy or {}).get("coding") or {}
    command = args.command or coding_block.get("test_command") or "pytest -x -q"

    try:
        result = await asyncio.wait_for(backend.execute(command), timeout=args.timeout_s)
    except TimeoutError:
        return {"ok": False, "reason": "timeout", "command": command}
    except Exception as e:
        return {
            "ok": False,
            "reason": "exec_error",
            "error": str(e),
            "command": command,
        }

    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    exit_code = int(getattr(result, "exit_code", -1))
    passed = exit_code == 0
    return {
        "ok": passed,
        "exit_code": exit_code,
        "command": command,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }
