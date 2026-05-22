"""SSH sandbox backend (M2.5.10).

A remote-execution backend that talks to a host the workspace already
has SSH credentials for. Layered fail-closed posture:

1. Platform admin must enable
   ``security.sandbox.allow_ssh_backend`` (default ``False``).
2. Private key must live in the vault — only ``vault://...`` refs
   accepted; plaintext keys raise at config-load time.
3. ``known_hosts_pin`` is mandatory; ``StrictHostKeyChecking=no`` has
   no representation in the schema and cannot be enabled.
4. In production, ``execute=True`` requires a non-empty
   ``command_allowlist``.
5. Each command goes through the standard approval queue with the
   same 5-minute TTL the rest of the runtime uses; the human sees
   the host, user, and exact command before approving.

Design choices worth keeping:

* No connection pool. A fresh ``asyncssh.SSHClientConnection`` opens
  per ``run_command`` so the audit trail has a clean 1:1 between
  approvals and SSH sessions. Pooling can land later when benchmarks
  justify the extra failure modes.
* Approval row uses ``resource_type=None`` + ``tool_name="ssh_execute"``
  so it rides the legacy tool-call path through
  :mod:`app.services.approval_dispatch` (no-op dispatch). Polling the
  approval row in DB also works because the M2.5 service writes a
  terminal status before returning.
* The vault read happens inside the lazy ``__aenter__`` rather than
  at config-parse time so the private key never sits in process
  memory longer than a single command.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.errors import (
    SandboxKindDisabled,
    SshCommandDenied,
    SshCommandRejected,
    SshConfigInvalid,
    SshKnownHostsMismatch,
)
from app.core.security import utcnow_naive
from app.db.models.approval import ApprovalStatus
from app.db.session import get_session_factory
from app.repositories.approval import ApprovalRepository
from app.services import audit as audit_svc
from app.services import vault as vault_svc

log = logging.getLogger(__name__)

VAULT_REF_PREFIX = "vault://"

# Output truncation cap. Audit metadata + tool result both honour this
# so the caller never sees more than 4 KB per stream — gigabyte stdout
# from a misbehaving command would otherwise blow up the JSONB column
# and the frontend tool-result panel.
_MAX_STREAM_BYTES = 4096


# ── Errors raised internally only ──────────────────────────────
class _SshSetupFailed(Exception):
    """Internal connect-time failure that should bubble up as a generic
    sandbox error string rather than a typed AppError.
    """


# ── Config schema ──────────────────────────────────────────────
class SshSandboxConfig(BaseModel):
    """Validated representation of ``policy["sandbox"]["ssh"]``.

    Pydantic enforces every field-level invariant; the cross-field
    production check lives in :func:`build_ssh_sandbox`.
    """

    host: str = Field(min_length=1, max_length=200)
    port: int = Field(ge=1, le=65535, default=22)
    user: str = Field(min_length=1, max_length=80)
    # The ``vault://...`` prefix is the contract — anything else means a
    # plaintext key snuck into config and we fail loud at parse time.
    private_key_ref: str = Field(min_length=1, pattern=r"^vault://.+$")
    # Required, no default. Operators who haven't pinned the host key
    # cannot bring up the backend; ``StrictHostKeyChecking=no`` is
    # never reachable from any code path.
    known_hosts_pin: str = Field(min_length=1, max_length=2000)
    execute: bool = False
    require_approval: bool = True
    command_allowlist: list[str] = Field(default_factory=list)
    connect_timeout_seconds: int = Field(ge=1, le=120, default=30)
    command_timeout_seconds: int = Field(ge=1, le=600, default=60)


# ── Result envelope ────────────────────────────────────────────
@dataclass(slots=True)
class SshCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    approved_by: uuid.UUID | None
    approval_id: uuid.UUID | None


# ── Vault parse helpers ────────────────────────────────────────
def parse_vault_ref(ref: str) -> tuple[str, str]:
    """Split ``vault://<scope>/<key>`` into ``(scope, key)``.

    Mirrors the substitution helper in :mod:`app.services.vault` —
    only ``workspace`` scope is allowed. Raises :class:`SshConfigInvalid`
    on any other shape so the failure surfaces at sandbox build time
    rather than connection time.
    """
    if not ref.startswith(VAULT_REF_PREFIX):
        raise SshConfigInvalid(
            "private_key_ref must use the vault:// scheme",
            code="sandbox.ssh_config_invalid",
            extras={"reason": "missing_vault_prefix"},
        )
    body = ref[len(VAULT_REF_PREFIX) :]
    scope, _, key = body.partition("/")
    if not scope or not key:
        raise SshConfigInvalid(
            "private_key_ref must be vault://<scope>/<key>",
            code="sandbox.ssh_config_invalid",
            extras={"reason": "malformed_vault_ref"},
        )
    if scope != "workspace":
        raise SshConfigInvalid(
            f"vault scope {scope!r} is not allowed for SSH keys; only 'workspace' is supported",
            code="sandbox.ssh_config_invalid",
            extras={"reason": "scope_not_allowed", "scope": scope},
        )
    return scope, key


def _hash_for_audit(value: str) -> str:
    """Short SHA-256 prefix used in audit metadata so raw secrets /
    workspace ids never land in log files. 16 hex chars = 64 bits, low
    collision risk for the population sizes we audit on.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


# ── Platform-settings gate ─────────────────────────────────────
async def _ssh_backend_enabled(db: AsyncSession) -> bool:
    """Read ``security.sandbox.allow_ssh_backend`` from the M0.13 store.

    Pulled into a helper so unit tests can monkeypatch without touching
    the full platform-settings import chain.
    """
    try:
        from app.services.platform_settings import (
            PlatformSettingsSection,
            get_section,
        )

        section = await get_section(db, section=PlatformSettingsSection.SECURITY_SANDBOX)
    except Exception as exc:
        log.warning(
            "ssh sandbox: platform_settings read failed (%s); treating allow_ssh_backend as False",
            exc,
        )
        return False
    return bool(getattr(section, "allow_ssh_backend", False))


async def assert_ssh_backend_allowed(db: AsyncSession, *, workspace_id: uuid.UUID) -> None:
    """Raise :class:`SandboxKindDisabled` when the platform admin has
    not enabled the SSH backend. Writes a single audit row so the
    operator sees attempted use even when the config never gets off
    the ground.
    """
    if await _ssh_backend_enabled(db):
        return
    await audit_svc.record(
        db,
        action="sandbox.ssh_kind_disabled",
        actor_identity_id=None,
        workspace_id=workspace_id,
        resource_type="sandbox",
        resource_id=None,
        summary="ssh sandbox requested while platform allow_ssh_backend=False",
        metadata={"workspace_id_hash": _hash_for_audit(str(workspace_id))},
    )
    raise SandboxKindDisabled(
        "SSH sandbox is not enabled at the platform level",
        code="sandbox.kind_disabled",
        extras={"setting": "security.sandbox.allow_ssh_backend"},
    )


# ── Build entry point ──────────────────────────────────────────
def _is_production() -> bool:
    return str(app_settings.APP_ENV).lower() == "production"


def validate_runtime_config(config: SshSandboxConfig) -> None:
    """Cross-field validation that depends on the runtime environment.

    Pydantic handles per-field shape; this is the production-only
    "execute=True must have a non-empty allowlist" guard explicitly
    called out by the M2.5.10 spec (roadmap line 1936). Kept as a
    standalone function so unit tests can exercise it without touching
    the platform-settings store.
    """
    if config.execute and _is_production() and not config.command_allowlist:
        raise SshConfigInvalid(
            "In production, sandbox 'kind=ssh' with 'execute=True' "
            "requires a non-empty command_allowlist",
            code="sandbox.ssh_config_invalid",
            extras={"reason": "production_requires_allowlist"},
        )


async def build_ssh_sandbox(
    *,
    db: AsyncSession,
    workspace_id: uuid.UUID,
    config_dict: dict[str, Any],
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    requested_by_identity_id: uuid.UUID | None = None,
) -> SshSandbox:
    """Validate the policy, gate on platform settings, return a
    ready-to-use :class:`SshSandbox`.

    The connection itself stays lazy — only the next ``run_command``
    actually opens a network socket. Vault reads are also deferred so
    the private key is never resident in the build path.
    """
    await assert_ssh_backend_allowed(db, workspace_id=workspace_id)

    config = SshSandboxConfig.model_validate(config_dict)

    # Validate the vault ref shape eagerly so a typo surfaces at build
    # time instead of on the first command attempt.
    parse_vault_ref(config.private_key_ref)

    validate_runtime_config(config)

    return SshSandbox(
        config=config,
        workspace_id=workspace_id,
        session_id=session_id,
        agent_id=agent_id,
        run_id=run_id,
        requested_by_identity_id=requested_by_identity_id,
    )


# ── Approval bridge ────────────────────────────────────────────
def _command_summary(host: str, user: str, command: str) -> str:
    cmd = command.strip().splitlines()[0][:120]
    return f"ssh {user}@{host}: {cmd}"


async def _create_ssh_approval(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    requested_by_identity_id: uuid.UUID | None,
    host: str,
    user: str,
    command: str,
    ttl_seconds: int = 300,
) -> uuid.UUID:
    """Persist a fresh approval row for an SSH command and return its id.

    Uses ``resource_type=None`` + ``tool_name='ssh_execute'`` so the
    M2.5 dispatch handler treats it as a legacy tool-call (no-op
    dispatch). The caller (``SshConnection.run_command``) waits for
    a terminal status before either running the command or raising
    :class:`SshCommandDenied`.
    """
    approval_id: uuid.UUID
    async with get_session_factory()() as db:
        repo = ApprovalRepository(db)
        row = await repo.create(
            workspace_id=workspace_id,
            session_id=session_id,
            agent_id=agent_id,
            run_id=run_id,
            tool_name="ssh_execute",
            tool_args={
                "host": host,
                "user": user,
                "command": command[:2000],
            },
            summary=_command_summary(host, user, command),
            requested_by_identity_id=requested_by_identity_id,
            expires_at=utcnow_naive() + timedelta(seconds=ttl_seconds),
        )
        approval_id = row.id
        await db.commit()
    return approval_id


async def _wait_for_approval_decision(
    approval_id: uuid.UUID,
    *,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 1.0,
) -> tuple[ApprovalStatus, uuid.UUID | None]:
    """Poll the approval row until it leaves ``PENDING`` or the TTL
    expires.

    DB polling rather than the in-memory ``ApprovalManager``: the SSH
    sandbox is expected to be invoked from contexts (workflow, batch
    sweep, no-agent flow) where the in-process future may not be the
    one that gets the decision; another worker may be running the
    REST decision endpoint. Polling is cheap (one PK lookup per
    second) and process-agnostic.

    On TTL expiry we flip the row to ``EXPIRED`` ourselves so the
    audit feed distinguishes user-denied from time-expired and the
    TTL processor doesn't have to do a second pass on the same row.
    Returns ``(status, decided_by_identity_id)``.
    """
    deadline = time.monotonic() + max(timeout_seconds, 1)

    while True:
        async with get_session_factory()() as db:
            repo = ApprovalRepository(db)
            row = await repo.get(approval_id)
            if row is None:
                return ApprovalStatus.DENIED, None
            if row.status != ApprovalStatus.PENDING:
                return row.status, row.decided_by_identity_id
        if time.monotonic() >= deadline:
            # Self-expire so the row leaves the pending queue without
            # waiting for the hourly TTL processor.
            async with get_session_factory()() as db:
                repo = ApprovalRepository(db)
                pending_row = await repo.get(approval_id)
                if pending_row is None:
                    return ApprovalStatus.DENIED, None
                await repo.decide(
                    approval_id=approval_id,
                    workspace_id=pending_row.workspace_id,
                    approved=False,
                    reason="timeout",
                    decided_by_identity_id=None,
                    now=utcnow_naive(),
                    status_override=ApprovalStatus.EXPIRED,
                )
                await db.commit()
            return ApprovalStatus.EXPIRED, None
        await asyncio.sleep(poll_interval_seconds)


# ── Lazy connection ────────────────────────────────────────────
class SshConnection:
    """Async context manager that opens an :mod:`asyncssh` connection
    on enter and closes it on exit.

    The connection only exists for the duration of one command so the
    audit trail has a clean 1:1 between approvals and live sessions.
    """

    def __init__(
        self,
        config: SshSandboxConfig,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        requested_by_identity_id: uuid.UUID | None = None,
    ) -> None:
        self.config = config
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.run_id = run_id
        self.requested_by_identity_id = requested_by_identity_id
        self._conn: Any | None = None

    async def __aenter__(self) -> SshConnection:
        try:
            import asyncssh
        except ImportError as exc:
            raise SshConfigInvalid(
                "asyncssh is not installed; install with the "
                "'ssh-sandbox' extra: pip install '.[ssh-sandbox]'",
                code="sandbox.ssh_config_invalid",
                extras={"reason": "asyncssh_missing"},
            ) from exc

        scope, key = parse_vault_ref(self.config.private_key_ref)
        try:
            async with get_session_factory()() as db:
                pem = await vault_svc.reveal_workspace_secret(
                    db, workspace_id=self.workspace_id, name=key
                )
                # Audit the resolution so a leaked vault key is visible
                # in the audit feed without leaking the key itself.
                await audit_svc.record(
                    db,
                    action="vault.private_key_resolved",
                    actor_identity_id=self.requested_by_identity_id,
                    workspace_id=self.workspace_id,
                    resource_type="sandbox",
                    resource_id=None,
                    summary=f"resolved private key {key!r} for ssh sandbox",
                    metadata={
                        "workspace_id_hash": _hash_for_audit(str(self.workspace_id)),
                        "key_ref_hash": _hash_for_audit(self.config.private_key_ref),
                    },
                )
                await db.commit()
        except vault_svc.VaultKeyNotFoundError:
            raise

        try:
            private_key = asyncssh.import_private_key(pem.encode("utf-8"))
            known_hosts = asyncssh.import_known_hosts(self.config.known_hosts_pin)
        except Exception as exc:
            raise _SshSetupFailed(f"failed to import ssh credentials ({exc!s})") from exc

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=self.config.host,
                    port=self.config.port,
                    username=self.config.user,
                    client_keys=[private_key],
                    known_hosts=known_hosts,
                    # asyncssh defaults to checking the user's
                    # ``~/.ssh/known_hosts`` if known_hosts is None or
                    # missing — passing the pinned object explicitly is
                    # the only path that reaches this line, but we set
                    # ``known_hosts_check=True`` documentation-style
                    # via keeping the arg above mandatory.
                ),
                timeout=self.config.connect_timeout_seconds,
            )
        except (asyncssh.HostKeyNotVerifiable, asyncssh.HostKeyAlgorithmError) as exc:
            await self._audit_known_hosts_mismatch(reason=str(exc))
            raise SshKnownHostsMismatch(
                "remote host key did not match the configured pin",
                code="sandbox.ssh_known_hosts_mismatch",
                extras={"host": self.config.host, "port": self.config.port},
            ) from exc
        except TimeoutError as exc:
            raise _SshSetupFailed(
                f"ssh connect timed out after {self.config.connect_timeout_seconds}s"
            ) from exc
        except Exception as exc:
            raise _SshSetupFailed(f"ssh connect failed: {exc!s}") from exc

        await self._audit_session_opened()
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
                # ``wait_closed`` returns a coroutine in asyncssh; await it
                # so the kernel's TCP teardown actually completes before
                # the next per-command connection attempt.
                await self._conn.wait_closed()
            except Exception as exc:
                log.warning("ssh sandbox: close failed (%s)", exc)
            finally:
                self._conn = None

    async def _audit_session_opened(self) -> None:
        try:
            async with get_session_factory()() as db:
                await audit_svc.record(
                    db,
                    action="sandbox.ssh_session_opened",
                    actor_identity_id=self.requested_by_identity_id,
                    workspace_id=self.workspace_id,
                    resource_type="sandbox",
                    resource_id=None,
                    summary=f"opened ssh session to {self.config.user}@{self.config.host}",
                    metadata={
                        "host": self.config.host,
                        "port": self.config.port,
                        "user": self.config.user,
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover - audit best-effort
            log.exception("audit ssh_session_opened failed")

    async def _audit_known_hosts_mismatch(self, *, reason: str) -> None:
        try:
            async with get_session_factory()() as db:
                await audit_svc.record(
                    db,
                    action="sandbox.ssh_known_hosts_mismatch",
                    actor_identity_id=self.requested_by_identity_id,
                    workspace_id=self.workspace_id,
                    resource_type="sandbox",
                    resource_id=None,
                    summary=(f"host key mismatch for {self.config.user}@{self.config.host}"),
                    metadata={
                        "host": self.config.host,
                        "port": self.config.port,
                        "reason": reason[:200],
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit ssh_known_hosts_mismatch failed")

    async def run_command(
        self,
        command: str,
        *,
        require_approval: bool | None = None,
        timeout: int | None = None,
    ) -> SshCommandResult:
        """Run ``command`` over the open SSH connection.

        Order of operations:

        1. Reject commands not in the allowlist (when one is set).
        2. If approval is required, create an approval row and wait
           for a terminal decision; raise :class:`SshCommandDenied` on
           anything other than ``APPROVED``.
        3. Open the connection lazily if it isn't already, run the
           command with the configured per-command timeout, capture
           stdout/stderr (truncated), and write a single
           ``sandbox.ssh_command_executed`` audit row.
        """
        require = self.config.require_approval if require_approval is None else require_approval
        cmd_timeout = timeout or self.config.command_timeout_seconds

        if not _command_in_allowlist(command, self.config.command_allowlist):
            await self._audit_command_rejected(command=command, reason="not_in_allowlist")
            raise SshCommandRejected(
                "command is not in the configured allowlist",
                code="sandbox.ssh_command_rejected",
                extras={"command_prefix": command.strip().split()[:1]},
            )

        approval_id: uuid.UUID | None = None
        approved_by: uuid.UUID | None = None
        if require:
            approval_id = await _create_ssh_approval(
                workspace_id=self.workspace_id,
                session_id=self.session_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                requested_by_identity_id=self.requested_by_identity_id,
                host=self.config.host,
                user=self.config.user,
                command=command,
            )
            status, approved_by = await _wait_for_approval_decision(approval_id)
            if status != ApprovalStatus.APPROVED:
                raise SshCommandDenied(
                    "ssh command was not approved",
                    code="sandbox.ssh_command_denied",
                    extras={
                        "approval_id": str(approval_id),
                        "status": status.value,
                    },
                )

        if self._conn is None:
            await self.__aenter__()

        started = time.monotonic()
        try:
            assert self._conn is not None  # for type-checkers; ensured above
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=cmd_timeout,
            )
        except TimeoutError as exc:
            raise _SshSetupFailed(f"ssh command timed out after {cmd_timeout}s") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _truncate(result.stdout)
        stderr = _truncate(result.stderr)
        exit_code = int(result.exit_status if result.exit_status is not None else -1)

        await self._audit_command_executed(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            approval_id=approval_id,
            approved_by=approved_by,
        )

        return SshCommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            approved_by=approved_by,
            approval_id=approval_id,
        )

    async def _audit_command_executed(
        self,
        *,
        command: str,
        exit_code: int,
        duration_ms: int,
        approval_id: uuid.UUID | None,
        approved_by: uuid.UUID | None,
    ) -> None:
        try:
            async with get_session_factory()() as db:
                await audit_svc.record(
                    db,
                    action="sandbox.ssh_command_executed",
                    actor_identity_id=approved_by or self.requested_by_identity_id,
                    workspace_id=self.workspace_id,
                    resource_type="sandbox",
                    resource_id=approval_id,
                    summary=_command_summary(self.config.host, self.config.user, command),
                    metadata={
                        "host": self.config.host,
                        "user": self.config.user,
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                        "approval_id": (str(approval_id) if approval_id else None),
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit ssh_command_executed failed")

    async def _audit_command_rejected(self, *, command: str, reason: str) -> None:
        try:
            async with get_session_factory()() as db:
                await audit_svc.record(
                    db,
                    action="sandbox.ssh_command_rejected",
                    actor_identity_id=self.requested_by_identity_id,
                    workspace_id=self.workspace_id,
                    resource_type="sandbox",
                    resource_id=None,
                    summary=_command_summary(self.config.host, self.config.user, command),
                    metadata={
                        "host": self.config.host,
                        "user": self.config.user,
                        "reason": reason,
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit ssh_command_rejected failed")


# ── Public Sandbox-shaped wrapper ──────────────────────────────
class SshSandbox:
    """Sandbox-shaped wrapper around :class:`SshConnection`.

    Each ``run_command`` / ``write_file`` / ``read_file`` call opens
    its own connection (no pooling). That keeps the audit trail clean
    per call and lets approvals act as the gate at exactly the right
    granularity.
    """

    def __init__(
        self,
        *,
        config: SshSandboxConfig,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        requested_by_identity_id: uuid.UUID | None = None,
    ) -> None:
        self.config = config
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.run_id = run_id
        self.requested_by_identity_id = requested_by_identity_id

    def _new_connection(self) -> SshConnection:
        return SshConnection(
            self.config,
            workspace_id=self.workspace_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            run_id=self.run_id,
            requested_by_identity_id=self.requested_by_identity_id,
        )

    async def run_command(
        self,
        command: str,
        *,
        require_approval: bool | None = None,
        timeout: int | None = None,
    ) -> SshCommandResult:
        # Don't open the connection eagerly — the approval gate inside
        # ``SshConnection.run_command`` must run first so a denied
        # request never produces network traffic. The connection then
        # opens on the first allowed command and we close it after.
        conn = self._new_connection()
        try:
            return await conn.run_command(
                command, require_approval=require_approval, timeout=timeout
            )
        finally:
            await conn.__aexit__(None, None, None)

    async def write_file(self, path: str, content: str) -> None:
        """SFTP write — destructive, so it walks its own approval row
        rather than reusing the shell allowlist.

        The approval surface uses ``tool_name='ssh_write_file'`` so the
        operator UI can render a different card than for shell. When
        ``require_approval`` is False on the config, the write proceeds
        directly (matching the ``run_command`` semantics).
        """
        if self.config.require_approval:
            approval_id = await _create_ssh_approval(
                workspace_id=self.workspace_id,
                session_id=self.session_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                requested_by_identity_id=self.requested_by_identity_id,
                host=self.config.host,
                user=self.config.user,
                command=f"sftp:put {path}",
            )
            status, _ = await _wait_for_approval_decision(approval_id)
            if status != ApprovalStatus.APPROVED:
                raise SshCommandDenied(
                    "ssh write_file was not approved",
                    code="sandbox.ssh_command_denied",
                    extras={"approval_id": str(approval_id), "status": status.value},
                )
        conn = self._new_connection()
        try:
            await conn.__aenter__()
            assert conn._conn is not None
            async with conn._conn.start_sftp_client() as sftp:
                async with sftp.open(path, "w") as fp:
                    await fp.write(content)
        finally:
            await conn.__aexit__(None, None, None)

    async def read_file(self, path: str) -> str:
        """SFTP read — non-destructive, no approval gate; the
        ``sandbox.ssh_session_opened`` audit row alone records that
        the workspace's private key crossed the wire.
        """
        conn = self._new_connection()
        try:
            await conn.__aenter__()
            assert conn._conn is not None
            async with conn._conn.start_sftp_client() as sftp:
                async with sftp.open(path, "r") as fp:
                    data = await fp.read()
        finally:
            await conn.__aexit__(None, None, None)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return data


# ── Internal helpers ───────────────────────────────────────────
def _command_in_allowlist(command: str, allowlist: list[str]) -> bool:
    """Permit ``command`` when its first token matches an entry in the
    allowlist, or when the allowlist is empty.

    Empty allowlist mirrors the M2.5.10 spec: dev environments can
    leave it open; production with ``execute=True`` is blocked at
    build time when the allowlist is empty (``validate_runtime_config``).

    The match is on the lexer-split first token so ``"ls -la"`` gates
    on ``ls`` rather than the full string. Operators who need
    sub-flag granularity should ship multiple allowlist entries.
    """
    if not allowlist:
        return True
    try:
        head = shlex.split(command)[0] if command.strip() else ""
    except ValueError:
        # Unbalanced quotes are a strong signal that the command was
        # constructed unsafely; reject rather than guess.
        return False
    if not head:
        return False
    return head in allowlist


def _truncate(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) <= _MAX_STREAM_BYTES:
        return text
    encoded = text.encode("utf-8")[:_MAX_STREAM_BYTES]
    decoded = encoded.decode("utf-8", errors="replace")
    return decoded + "...[truncated]"


__all__ = [
    "VAULT_REF_PREFIX",
    "SshCommandResult",
    "SshConnection",
    "SshSandbox",
    "SshSandboxConfig",
    "assert_ssh_backend_allowed",
    "build_ssh_sandbox",
    "parse_vault_ref",
    "validate_runtime_config",
]
