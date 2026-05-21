"""Sandbox harness — wraps ``pydantic-ai-backends`` for Agent filesystem + shell.

Three execution modes selected via ``metadata_json.sandbox``:

    ``"docker"`` / ``{"kind": "docker", ...}``
        Per-session Docker container from ``DockerSandbox``. Full Python + shell.
        Requires the backend container to have access to a Docker daemon.

    ``"local"``
        ``LocalBackend`` at ``/data/storage/sandbox/{session_id}/``. Filesystem
        reads/writes are path-scoped to that directory. ``execute`` (arbitrary
        shell) is **disabled by default** and must be explicitly opted into.

    ``"state"``
        In-memory ``StateBackend`` (no persistence, no shell). Safe default
        for pure-Python notebook-style sessions.

    ``true`` (shortcut) → ``local`` with shell disabled.
    omitted / ``false`` → sandbox disabled, ``ConsoleCapability`` not attached.

Security defaults (tightened in V1):

    * ``execute`` defaults to ``False`` — opt-in only. Tools that run arbitrary
      shell code are the highest-risk surface in an agent, so they should not
      be enabled unless the agent author explicitly asks for them.
    * ``permissions`` defaults to ``"default"`` (not ``"permissive"``) — deny
      dangerous commands unless the policy lists them.
    * ``require_approval`` defaults to ``True`` — every ``execute`` and
      destructive filesystem write goes through the HITL approval queue.

Production guards:

    * When ``APP_ENV=production`` we refuse to start a session with
      ``kind=local`` AND ``execute=True`` UNLESS the operator has set
      ``SANDBOX_LOCAL_EXECUTE_PROD=true`` on the backend container. Arbitrary
      shell on the backend process host is almost never intended in prod.
    * The Docker socket mount pattern (`/var/run/docker.sock`) equates to root
      on the host; prod deployments should swap ``kind=docker`` for
      rootless-podman / sysbox / gVisor / remote DinD. This is a deployment
      concern documented in ``docs/deployment.md``; the runtime does not
      introspect the host itself.

Returns ``(ConsoleCapability, backend)`` so the runner can install the backend
onto ``SenHarnessDeps.backend`` **before** invoking the agent.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


class SandboxMisconfiguredError(ValueError):
    """Raised when a sandbox policy would be unsafe for the current env.

    The runner layer catches this and surfaces it as an ``error`` RunEvent so
    the chat UI shows the specific reason (e.g. "local+execute in prod").
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# ─── Active-backend registry ────────────────────────────────────
# The runner installs the live backend here per run so tools that need shell
# (e.g. ``run_tests``) can reach it without threading RunContext everywhere.
# Keys are stringified run_ids; values are the pydantic-ai Backend objects.
_ACTIVE_BACKENDS: dict[str, Any] = {}


def register_active_backend(run_id: Any, backend: Any) -> None:
    if backend is not None:
        _ACTIVE_BACKENDS[str(run_id)] = backend


def unregister_active_backend(run_id: Any) -> None:
    _ACTIVE_BACKENDS.pop(str(run_id), None)


def active_backend_for(run_id: Any) -> Any | None:
    return _ACTIVE_BACKENDS.get(str(run_id))


def build_sandbox(
    *, policy: dict[str, Any] | None
) -> tuple[Any | None, Any | None]:
    """Return ``(capability, backend)`` — both ``None`` when sandbox disabled.

    Raises :class:`SandboxMisconfigured` if the requested policy is unsafe for
    the current environment (e.g. ``kind=local + execute=True`` in prod).
    """
    spec = _normalize(policy)
    if spec is None:
        return None, None

    kind = spec.get("kind", "local")

    # SSH backend (M2.5.10) needs an :class:`AsyncSession` (platform-
    # settings gate + vault read) and a workspace id. The sync
    # ``build_sandbox`` cannot supply either, so route the caller to
    # :func:`build_ssh_sandbox` instead of silently degrading.
    if kind == "ssh":
        raise SandboxMisconfiguredError(
            "sandbox kind='ssh' must be built via "
            "``app.agents.harness.sandbox.build_ssh_sandbox(...)`` "
            "(async, requires AsyncSession + workspace_id).",
            code="sandbox.ssh_requires_async_builder",
        )

    # ── Opt-in flags (secure defaults) ────────────────────────
    # ``execute``: arbitrary shell. Default False — agents that need a shell
    # must explicitly set ``execute: true`` in their metadata.
    include_execute = bool(spec.get("execute", False))

    # Guard: production never allows ``kind=local + execute=True`` unless the
    # operator explicitly opts in via env var. The local backend runs in the
    # same process / container as SenHarness backend, so arbitrary shell there
    # is equivalent to compromising SenHarness itself. This must run BEFORE
    # the optional-dependency import check so a missing package can't bypass
    # the guard (defence in depth).
    if (
        include_execute
        and kind == "local"
        and str(settings.APP_ENV).lower() == "production"
        and not getattr(settings, "SANDBOX_LOCAL_EXECUTE_PROD", False)
    ):
        raise SandboxMisconfiguredError(
            "In production, sandbox 'kind=local' with 'execute=True' is "
            "blocked because it would run shell on the SenHarness backend "
            "host. Either switch to kind='docker' / 'state', or set "
            "SANDBOX_LOCAL_EXECUTE_PROD=true to override after reviewing the "
            "security tradeoff.",
            code="sandbox.local_execute_blocked_in_prod",
        )

    try:
        from pydantic_ai_backends import ConsoleCapability
        from pydantic_ai_backends.backends import LocalBackend, StateBackend
        from pydantic_ai_backends.toolsets.console import (
            create_console_toolset,
        )
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-backend not installed; sandbox disabled")
        return None, None

    backend: Any = None

    if kind == "docker":
        backend = _build_docker_backend(spec, policy=policy)
    elif kind == "state":
        backend = StateBackend()
    else:  # local default
        session_id = (policy or {}).get("session_id") or uuid.uuid4().hex
        root = Path(settings.STORAGE_LOCAL_PATH) / "sandbox" / str(session_id)
        root.mkdir(parents=True, exist_ok=True)
        backend = LocalBackend(
            root_dir=str(root),
            enable_execute=include_execute,
            sandbox_id=f"senharness-local-{session_id}",
        )

    if backend is None:
        return None, None

    # ``permissions``: default ruleset (deny dangerous commands). Operators
    # who want the previous behaviour can set ``permissions: "permissive"``.
    ruleset = _resolve_ruleset(spec.get("permissions", "default"))

    try:
        # ``require_approval``: every execute/write goes through HITL unless
        # the agent policy explicitly opts out. The runner bridges
        # DeferredToolCall → ``approvals.resolve_require_approval`` so the
        # approval card appears in the chat UI; workspace admins can still
        # globally disable approvals on an agent by setting this False.
        require_approval = bool(spec.get("require_approval", True))

        cap = ConsoleCapability(
            include_execute=include_execute,
            permissions=ruleset,
        )
        cap._toolset = create_console_toolset(  # type: ignore[attr-defined]
            include_execute=include_execute,
            require_write_approval=require_approval,
            require_execute_approval=require_approval,
            permissions=ruleset,
        )
    except Exception as e:  # pragma: no cover
        log.warning("ConsoleCapability init failed: %s", e)
        return None, None

    return cap, backend


def _resolve_ruleset(name: Any) -> Any:
    try:
        from pydantic_ai_backends.permissions import (
            DEFAULT_RULESET,
            PERMISSIVE_RULESET,
            READONLY_RULESET,
            STRICT_RULESET,
        )
    except ImportError:
        return None
    mapping = {
        "permissive": PERMISSIVE_RULESET,
        "default": DEFAULT_RULESET,
        "readonly": READONLY_RULESET,
        "strict": STRICT_RULESET,
    }
    if isinstance(name, str):
        return mapping.get(name.lower(), DEFAULT_RULESET)
    return DEFAULT_RULESET


def _normalize(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    if not policy:
        return None
    spec = policy.get("sandbox")
    if spec is None or spec is False:
        return None
    if spec is True:
        # Shortcut ``true`` means "I want a sandbox" — we pick the safe
        # default (local filesystem scoping, no shell, approvals on).
        return {"kind": "local"}
    if isinstance(spec, str):
        return {"kind": spec.lower()}
    if isinstance(spec, dict):
        return {**spec, "kind": str(spec.get("kind", "local")).lower()}
    return None


async def build_ssh_sandbox(
    *,
    policy: dict[str, Any] | None,
    db: Any,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    requested_by_identity_id: uuid.UUID | None = None,
) -> Any:
    """Async builder for the SSH sandbox backend (M2.5.10).

    Steps:

    1. Normalise ``policy`` exactly like :func:`build_sandbox`.
    2. Confirm ``kind == "ssh"`` and the ``ssh`` block is present.
    3. Delegate to :func:`app.services.sandbox_ssh.build_ssh_sandbox`
       which gates on platform settings, parses the typed config, and
       returns an :class:`SshSandbox`.

    Raises :class:`SandboxMisconfiguredError` when the policy isn't an
    SSH spec; callers that wired the wrong ``kind`` should fall back
    to the sync :func:`build_sandbox` path.
    """
    spec = _normalize(policy)
    if spec is None or spec.get("kind") != "ssh":
        raise SandboxMisconfiguredError(
            "build_ssh_sandbox called without sandbox.kind='ssh'",
            code="sandbox.kind_mismatch",
        )

    ssh_block = spec.get("ssh") or {}
    if not isinstance(ssh_block, dict):
        raise SandboxMisconfiguredError(
            "policy.sandbox.ssh must be an object",
            code="sandbox.ssh_block_invalid",
        )

    # Late import: keeps the sync ``build_sandbox`` import-time graph
    # free of services / DB dependencies, which matters because the
    # native runner imports this module at startup.
    from app.services.sandbox_ssh import build_ssh_sandbox as _service_build

    return await _service_build(
        db=db,
        workspace_id=workspace_id,
        config_dict=ssh_block,
        session_id=session_id,
        agent_id=agent_id,
        run_id=run_id,
        requested_by_identity_id=requested_by_identity_id,
    )


def _build_docker_backend(spec: dict[str, Any], *, policy: dict[str, Any] | None) -> Any | None:
    try:
        from pydantic_ai_backends.backends.docker import (
            BUILTIN_RUNTIMES,
            DockerSandbox,
        )
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-backend docker extras missing")
        return None

    image = spec.get("image") or "python:3.12-slim"
    runtime = spec.get("runtime")
    if isinstance(runtime, str) and runtime in BUILTIN_RUNTIMES:
        runtime_cfg: Any = BUILTIN_RUNTIMES[runtime]
    else:
        runtime_cfg = None

    work_dir = spec.get("work_dir") or "/workspace"
    session_id = (policy or {}).get("session_id") or uuid.uuid4().hex

    try:
        kwargs: dict[str, Any] = {
            "image": image,
            "work_dir": work_dir,
            "auto_remove": True,
            "session_id": str(session_id)[:32],
            "idle_timeout": int(spec.get("idle_timeout", 1800)),
        }
        if runtime_cfg is not None:
            kwargs["runtime"] = runtime_cfg
        if spec.get("network_mode"):
            kwargs["network_mode"] = spec["network_mode"]
        return DockerSandbox(**kwargs)
    except Exception as e:  # pragma: no cover
        log.warning("DockerSandbox init failed: %s (is Docker socket mounted?)", e)
        return None
