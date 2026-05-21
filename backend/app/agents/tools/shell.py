"""`shell` — run an arbitrary shell command inside the agent's sandbox.

The single most dangerous tool in the registry: it lets the model run any
command. We mitigate this with three layers of defence, all of which must
hold for the call to actually execute:

1. **Default-off registration.** The tool is *not* in ``DEFAULT_TOOLBOX`` or
   ``CODING_TOOLBOX``; an agent must opt in explicitly via its
   ``metadata.tools.builtin`` allowlist (or template).
2. **Sandbox-kind whitelist.** This runner refuses any sandbox kind other
   than ``docker``. The ``local`` backend would put shell on the SenHarness
   container itself; ``state`` has no shell at all. So we hard-fail with
   ``sandbox_required`` when ``policy.sandbox.kind != "docker"``.
3. **HITL approval.** The underlying ``ConsoleCapability`` is built with
   ``require_execute_approval=True`` (see :mod:`app.agents.harness.sandbox`).
   The runner intercepts the deferred call and pushes an
   ``approval_request`` frame; the user must approve in the chat UI before
   the command actually runs.

Returns ``{ok, exit_code, command, stdout, stderr, truncated}`` on success,
``{ok: false, reason: <code>, ...}`` on rejection / failure.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from pydantic import BaseModel, Field

from app.agents.harness.sandbox import active_backend_for
from app.agents.tools._context import get_context


class ShellArgs(BaseModel):
    command: str = Field(
        ...,
        description=(
            "Shell command to run inside the agent's Docker sandbox. "
            "Subject to the workspace permission ruleset (default: deny "
            "dangerous operations) and the HITL approval queue."
        ),
    )
    timeout_s: int = Field(
        default=60,
        ge=5,
        le=300,
        description="Hard timeout in seconds.",
    )
    cwd: str | None = Field(
        default=None,
        description=(
            "Optional working directory inside the sandbox (relative to "
            "the sandbox root). The runner prepends ``cd <cwd> && `` to "
            "the command — pass a path that already exists in the image."
        ),
    )


def _compose_command(command: str, cwd: str | None) -> str:
    """Prepend a ``cd`` to ``command`` when ``cwd`` is set.

    The underlying ``SandboxProtocol.execute`` only takes a single command
    string; emulating ``cwd`` via ``cd … && …`` keeps the surface area
    small and works on every backend that supports a POSIX shell.
    """
    if not cwd:
        return command
    quoted = shlex.quote(cwd)
    return f"cd {quoted} && {command}"


async def run_shell(args: ShellArgs) -> dict[str, Any]:
    ctx = get_context()
    policy = ctx.policy or {}
    sandbox_spec = policy.get("sandbox")

    # Normalise the sandbox shorthand to a kind. ``True`` and a bare
    # string are both valid in the policy schema, mirroring the logic
    # in :mod:`app.agents.harness.sandbox`.
    if isinstance(sandbox_spec, dict):
        kind = str(sandbox_spec.get("kind", "local")).lower()
    elif isinstance(sandbox_spec, str):
        kind = sandbox_spec.lower()
    else:
        kind = ""

    if kind != "docker":
        return {
            "ok": False,
            "reason": "sandbox_required",
            "hint": (
                "shell is only available with sandbox.kind=docker. Set "
                "metadata.sandbox = {kind: 'docker', execute: true} on "
                "the agent and ensure the backend host has Docker."
            ),
        }

    backend = active_backend_for(ctx.run_id)
    if backend is None:
        return {
            "ok": False,
            "reason": "no_sandbox",
            "hint": (
                "No active sandbox backend. The runner skipped sandbox "
                "construction (likely because pydantic-ai-backends is "
                "missing or the Docker daemon is unreachable)."
            ),
        }

    composed = _compose_command(args.command, args.cwd)

    # ``execute`` may be sync or async depending on the backend. Wrap in
    # ``asyncio.wait_for`` either way so we never block the WS event loop
    # past the user-specified timeout.
    try:
        execute = backend.execute
        if asyncio.iscoroutinefunction(execute):
            result = await asyncio.wait_for(
                execute(composed, timeout=args.timeout_s),
                timeout=args.timeout_s + 5,
            )
        else:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: execute(composed, timeout=args.timeout_s),
                ),
                timeout=args.timeout_s + 5,
            )
    except TimeoutError:
        return {
            "ok": False,
            "reason": "timeout",
            "command": args.command,
            "cwd": args.cwd,
        }
    except Exception as e:
        return {
            "ok": False,
            "reason": "exec_error",
            "error": str(e),
            "command": args.command,
            "cwd": args.cwd,
        }

    # ``ExecuteResponse`` from pydantic-ai-backends only carries a single
    # ``output`` field; some adapters add ``stdout`` / ``stderr`` for
    # parity with ``run_tests``. Surface whatever's available, capped to
    # 4 KiB each so the WS frame stays under the broker limit.
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    output = getattr(result, "output", None)
    if stdout is None and stderr is None and output is not None:
        # Single-stream response — surface as stdout for the UI.
        stdout = output
        stderr = ""

    exit_code = getattr(result, "exit_code", None)
    if exit_code is None:
        # Some backends may not surface an exit code; treat presence of
        # any output without an explicit error as success.
        exit_code = 0
    exit_code = int(exit_code)
    passed = exit_code == 0
    return {
        "ok": passed,
        "exit_code": exit_code,
        "command": args.command,
        "cwd": args.cwd,
        "stdout": (stdout or "")[-4000:],
        "stderr": (stderr or "")[-4000:],
        "truncated": bool(getattr(result, "truncated", False)),
    }
