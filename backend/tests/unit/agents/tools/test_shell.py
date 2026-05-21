"""Unit tests for the ``shell`` builtin tool.

The shell tool is the most dangerous surface in the registry, so the
contract we care about is *defensive* — every misconfiguration must be
caught with a structured ``{ok: false, reason: <code>}`` reply rather
than a stack trace, and the sandbox-kind whitelist must not be
bypassable. We exercise four paths here:

1. ``no_sandbox``        — no live backend registered for the run id.
2. ``sandbox_required``  — sandbox.kind != "docker" (covers `local`,
                            `state`, `True` shorthand and bare strings).
3. happy path            — docker backend mocked, command returns ok.
4. ``timeout``           — backend.execute hangs past ``timeout_s``.

We deliberately avoid spinning up a real Docker daemon — the tool's
contract is "delegate to the active backend", not "operate Docker".
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agents.harness.sandbox import (
    register_active_backend,
    unregister_active_backend,
)
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.shell import ShellArgs, _compose_command, run_shell

# ─── Fakes ────────────────────────────────────────────────────


class _ExecResp:
    """Minimal stand-in for ``pydantic_ai_backends.types.ExecuteResponse``."""

    def __init__(
        self,
        *,
        output: str = "",
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int = 0,
        truncated: bool = False,
    ) -> None:
        self.output = output
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.truncated = truncated


class _SyncBackend:
    """Backend whose ``execute`` is synchronous (mirrors LocalBackend)."""

    def __init__(self, response: _ExecResp) -> None:
        self.response = response
        self.calls: list[tuple[str, int | None]] = []

    def execute(self, command: str, timeout: int | None = None) -> _ExecResp:
        self.calls.append((command, timeout))
        return self.response


class _AsyncBackend:
    """Backend whose ``execute`` is async (mirrors hypothetical Docker)."""

    def __init__(self, response: _ExecResp) -> None:
        self.response = response
        self.calls: list[tuple[str, int | None]] = []

    async def execute(self, command: str, timeout: int | None = None) -> _ExecResp:
        self.calls.append((command, timeout))
        return self.response


class _HangingAsyncBackend:
    """Backend whose ``execute`` blocks forever — used for the timeout test."""

    async def execute(self, command: str, timeout: int | None = None) -> _ExecResp:
        await asyncio.sleep(60)
        return _ExecResp()  # pragma: no cover


# ─── Fixtures ─────────────────────────────────────────────────


def _ctx(*, policy: dict[str, Any] | None, run_id: uuid.UUID) -> ToolRunContext:
    return ToolRunContext(
        run_id=run_id,
        workspace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        scratch_base=Path("/tmp/shell-test"),
        policy=policy or {},
    )


@pytest.fixture
def fresh_run_id() -> uuid.UUID:
    """Each test gets its own run id so the active-backend registry can't
    leak across cases."""
    rid = uuid.uuid4()
    yield rid
    unregister_active_backend(rid)


# ─── Tests ────────────────────────────────────────────────────


class TestSandboxRequired:
    """The shell tool must refuse anything that isn't sandbox.kind=docker."""

    @pytest.mark.parametrize(
        "policy",
        [
            {},  # no sandbox key
            {"sandbox": False},  # explicitly disabled
            {"sandbox": True},  # shorthand → local
            {"sandbox": "local"},  # bare string
            {"sandbox": "state"},
            {"sandbox": {"kind": "local", "execute": True}},
            {"sandbox": {"kind": "STATE"}},  # case sensitivity
            {"sandbox": 42},  # garbage
        ],
    )
    async def test_non_docker_kind_is_rejected(
        self, policy: dict[str, Any], fresh_run_id: uuid.UUID
    ) -> None:
        # Even when an active backend exists, the policy gate fires first.
        register_active_backend(fresh_run_id, _SyncBackend(_ExecResp()))
        set_context(_ctx(policy=policy, run_id=fresh_run_id))
        result = await run_shell(ShellArgs(command="echo hi"))
        assert result["ok"] is False
        assert result["reason"] == "sandbox_required"
        assert "docker" in result["hint"].lower()


class TestNoSandbox:
    """``sandbox.kind=docker`` but no live backend registered → no_sandbox."""

    async def test_missing_active_backend(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        # Note: we deliberately DO NOT call ``register_active_backend``.
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="echo hi"))
        assert result["ok"] is False
        assert result["reason"] == "no_sandbox"


class TestHappyPath:
    """Docker sandbox + live backend + command returns ok."""

    async def test_sync_backend_with_split_streams(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        backend = _SyncBackend(
            _ExecResp(stdout="hello\n", stderr="", exit_code=0)
        )
        register_active_backend(fresh_run_id, backend)
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="echo hello"))
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result["command"] == "echo hello"
        assert result["stdout"] == "hello\n"
        assert backend.calls == [("echo hello", 60)]

    async def test_async_backend_with_combined_output(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        backend = _AsyncBackend(
            _ExecResp(output="combined", exit_code=0)
        )
        register_active_backend(fresh_run_id, backend)
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="ls"))
        assert result["ok"] is True
        # When ``output`` is the only stream, the runner surfaces it as stdout
        # so the UI Terminal tab can render it consistently.
        assert result["stdout"] == "combined"
        assert result["stderr"] == ""

    async def test_cwd_is_prepended_to_command(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        backend = _SyncBackend(_ExecResp(stdout="ok", exit_code=0))
        register_active_backend(fresh_run_id, backend)
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(
            ShellArgs(command="pwd", cwd="/workspace/sub")
        )
        assert result["ok"] is True
        assert result["cwd"] == "/workspace/sub"
        # The composed command should chdir before running ``pwd``.
        assert backend.calls[0][0].startswith("cd ")
        assert "/workspace/sub" in backend.calls[0][0]
        assert backend.calls[0][0].endswith("&& pwd")

    async def test_non_zero_exit_marks_failure(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        backend = _SyncBackend(
            _ExecResp(stdout="", stderr="boom\n", exit_code=2)
        )
        register_active_backend(fresh_run_id, backend)
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="false"))
        assert result["ok"] is False
        assert result["exit_code"] == 2
        # Non-zero exit isn't a *reason* — only structural failures are.
        assert "reason" not in result
        assert result["stderr"] == "boom\n"

    async def test_truncated_flag_passes_through(
        self, fresh_run_id: uuid.UUID
    ) -> None:
        backend = _SyncBackend(
            _ExecResp(stdout="x", exit_code=0, truncated=True)
        )
        register_active_backend(fresh_run_id, backend)
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="cat huge.log"))
        assert result["ok"] is True
        assert result["truncated"] is True


class TestTimeout:
    """When ``backend.execute`` exceeds ``timeout_s + 5``, we raise the
    structured timeout reason rather than letting the WS task hang.

    Note: the runner adds a 5-second grace to the user-supplied timeout
    so the underlying backend has a chance to honour ``timeout=`` itself.
    For the test we drop ``timeout_s`` to the minimum (5s) and intercept
    ``asyncio.wait_for`` so we don't actually wait 10 seconds.
    """

    async def test_async_backend_timeout(
        self,
        fresh_run_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_active_backend(fresh_run_id, _HangingAsyncBackend())
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )

        # Patch ``asyncio.wait_for`` in the tool module to close the
        # never-awaited coroutine and raise immediately. Closing the
        # coroutine avoids a ``RuntimeWarning`` in pytest output.
        async def _instant_timeout(coro: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                if hasattr(coro, "close"):
                    coro.close()
            finally:
                raise TimeoutError

        from app.agents.tools import shell as shell_mod

        monkeypatch.setattr(shell_mod.asyncio, "wait_for", _instant_timeout)

        result = await run_shell(ShellArgs(command="sleep 999", timeout_s=5))
        assert result["ok"] is False
        assert result["reason"] == "timeout"
        assert result["command"] == "sleep 999"

    async def test_unexpected_exception_is_reported_as_exec_error(
        self,
        fresh_run_id: uuid.UUID,
    ) -> None:
        class _Boom:
            def execute(self, command: str, timeout: int | None = None) -> Any:
                raise RuntimeError("permission denied: docker.sock")

        register_active_backend(fresh_run_id, _Boom())
        set_context(
            _ctx(
                policy={"sandbox": {"kind": "docker"}},
                run_id=fresh_run_id,
            )
        )
        result = await run_shell(ShellArgs(command="ls"))
        assert result["ok"] is False
        assert result["reason"] == "exec_error"
        assert "docker.sock" in result["error"]


# ─── Helper smoke tests ───────────────────────────────────────


class TestComposeCommand:
    def test_no_cwd_returns_command_unchanged(self) -> None:
        assert _compose_command("ls -la", None) == "ls -la"
        assert _compose_command("ls -la", "") == "ls -la"

    def test_cwd_is_prepended_with_cd(self) -> None:
        assert _compose_command("ls", "/workspace") == "cd /workspace && ls"

    def test_cwd_with_spaces_is_quoted(self) -> None:
        out = _compose_command("ls", "/path with spaces")
        # ``shlex.quote`` chooses single quotes for safety.
        assert out.startswith("cd '")
        assert "/path with spaces" in out


# ─── Registry sanity ──────────────────────────────────────────


class TestRegistry:
    def test_shell_is_registered(self) -> None:
        from app.agents.tools import (
            BUILTIN_TOOL_REGISTRY,
            CODING_TOOLBOX,
            DEFAULT_TOOLBOX,
        )

        assert "shell" in BUILTIN_TOOL_REGISTRY
        # Default-OFF: must not appear in either preset toolbox.
        assert "shell" not in CODING_TOOLBOX
        assert "shell" not in DEFAULT_TOOLBOX
