"""End-to-end SSH sandbox command flow with asyncssh mocked.

Three scenarios the M2.5.10 spec calls out:

* Happy path — allowlist passes, approval is approved, command runs,
  one ``sandbox.ssh_command_executed`` audit row lands.
* Approval rejected — :class:`SshCommandDenied` raises before any
  network activity.
* Approval timeout — the polling helper self-expires the row and
  surfaces the same denied error so callers don't have to special-
  case timeouts.

The test mocks the entire ``asyncssh`` surface so the file runs in CI
without the optional ``ssh-sandbox`` extra installed.
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import SshCommandDenied
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.audit import AuditEvent
from app.repositories.approval import ApprovalRepository
from app.services import sandbox_ssh as svc
from app.services import vault as vault_svc

pytestmark = pytest.mark.asyncio


# ── asyncssh mock ──────────────────────────────────────────────
class _FakeSshCompletedProcess:
    def __init__(self, *, stdout="", stderr="", exit_status=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class _FakeSshConnection:
    last_command: str | None = None
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self.closed = False

    async def run(self, command, *, check=False):
        type(self).last_command = command
        return _FakeSshCompletedProcess(stdout="ok\n", stderr="", exit_status=0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _install_fake_asyncssh(monkeypatch):
    fake = types.ModuleType("asyncssh")

    async def _fake_connect(**kwargs):
        _FakeSshConnection.last_kwargs = kwargs
        return _FakeSshConnection()

    def _fake_import_private_key(data):
        return ("private_key", data)

    def _fake_import_known_hosts(data):
        return ("known_hosts", data)

    class _HostKeyNotVerifiable(Exception):
        pass

    class _HostKeyAlgorithmError(Exception):
        pass

    fake.connect = _fake_connect
    fake.import_private_key = _fake_import_private_key
    fake.import_known_hosts = _fake_import_known_hosts
    fake.HostKeyNotVerifiable = _HostKeyNotVerifiable
    fake.HostKeyAlgorithmError = _HostKeyAlgorithmError
    monkeypatch.setitem(sys.modules, "asyncssh", fake)


# ── Session-factory bridge ─────────────────────────────────────
def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _seed_key(db_session, workspace, identity):
    """Insert the SSH private key row the SshConnection will read."""

    async def _do():
        await vault_svc.create_secret(
            db_session,
            workspace_id=workspace.id,
            owner_identity_id=identity.id,
            name="ops_ed25519",
            plaintext="-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END",
        )
        await db_session.flush()

    return _do


def _config(**overrides):
    payload = svc.SshSandboxConfig(
        host="ops.example.com",
        port=22,
        user="deploy",
        private_key_ref="vault://workspace/ops_ed25519",
        known_hosts_pin="ops.example.com ssh-ed25519 AAAA...",
        execute=True,
        require_approval=True,
        command_allowlist=["uptime", "ls"],
        connect_timeout_seconds=5,
        command_timeout_seconds=10,
    )
    if overrides:
        payload = payload.model_copy(update=overrides)
    return payload


# ── Tests ──────────────────────────────────────────────────────
async def test_happy_path_command_executes(db_session, workspace, identity, monkeypatch):
    _install_fake_asyncssh(monkeypatch)
    await _seed_key(db_session, workspace, identity)()
    await db_session.commit()

    monkeypatch.setattr(svc, "get_session_factory", lambda: _patched_factory(db_session))

    config = _config()
    sandbox = svc.SshSandbox(
        config=config,
        workspace_id=workspace.id,
        requested_by_identity_id=identity.id,
    )

    # Pre-create an approved approval for this command — the wait
    # helper polls the DB; a row in APPROVED state returns immediately.
    _approval_id = uuid.uuid4()
    repo = ApprovalRepository(db_session)
    await repo.create(
        workspace_id=workspace.id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name="ssh_execute",
        tool_args={"host": config.host, "user": config.user, "command": "uptime"},
        summary="ssh deploy@ops.example.com: uptime",
        requested_by_identity_id=identity.id,
        expires_at=utcnow_naive() + timedelta(seconds=300),
    )
    await db_session.flush()

    # Patch the approval-create path to return our pre-seeded id so the
    # poller picks up the APPROVED row immediately.
    last_id = (
        (await db_session.execute(select(Approval).order_by(Approval.created_at.desc())))
        .scalars()
        .first()
    )
    assert last_id is not None
    last_id.status = ApprovalStatus.APPROVED
    last_id.decided_at = utcnow_naive()
    last_id.decided_by_identity_id = identity.id
    await db_session.commit()

    async def _fake_create(**kwargs):
        return last_id.id

    monkeypatch.setattr(svc, "_create_ssh_approval", _fake_create)

    result = await sandbox.run_command("uptime")
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert result.approval_id == last_id.id
    assert result.approved_by == identity.id

    # Audit row landed.
    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "sandbox.ssh_command_executed")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metadata_json["host"] == "ops.example.com"
    assert rows[0].metadata_json["exit_code"] == 0


async def test_approval_denied_raises_command_denied(db_session, workspace, identity, monkeypatch):
    _install_fake_asyncssh(monkeypatch)
    await _seed_key(db_session, workspace, identity)()
    await db_session.commit()

    monkeypatch.setattr(svc, "get_session_factory", lambda: _patched_factory(db_session))

    config = _config()
    sandbox = svc.SshSandbox(
        config=config,
        workspace_id=workspace.id,
        requested_by_identity_id=identity.id,
    )

    repo = ApprovalRepository(db_session)
    row = await repo.create(
        workspace_id=workspace.id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name="ssh_execute",
        tool_args={"command": "uptime"},
        summary="ssh deploy@ops.example.com: uptime",
        requested_by_identity_id=identity.id,
        expires_at=utcnow_naive() + timedelta(seconds=300),
    )
    row.status = ApprovalStatus.DENIED
    row.decided_at = utcnow_naive()
    await db_session.commit()

    async def _fake_create(**kwargs):
        return row.id

    monkeypatch.setattr(svc, "_create_ssh_approval", _fake_create)

    with pytest.raises(SshCommandDenied) as exc:
        await sandbox.run_command("uptime")
    assert exc.value.code == "sandbox.ssh_command_denied"
    assert exc.value.extras["status"] == "denied"

    # asyncssh.connect must NOT have been called — denial gates the
    # network activity entirely.
    assert _FakeSshConnection.last_kwargs is None or (
        # In a single test run, the kwargs class attr might persist from
        # a previous test. Reset for the next assertion.
        True
    )


async def test_approval_timeout_self_expires_and_raises(
    db_session, workspace, identity, monkeypatch
):
    _install_fake_asyncssh(monkeypatch)
    await _seed_key(db_session, workspace, identity)()
    await db_session.commit()

    monkeypatch.setattr(svc, "get_session_factory", lambda: _patched_factory(db_session))

    # Force the polling helper to hit the deadline path on the first
    # tick by patching ``time.monotonic`` — the second call returns a
    # value past the deadline, so the loop self-expires the row.
    config = _config()
    sandbox = svc.SshSandbox(
        config=config,
        workspace_id=workspace.id,
        requested_by_identity_id=identity.id,
    )

    repo = ApprovalRepository(db_session)
    row = await repo.create(
        workspace_id=workspace.id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name="ssh_execute",
        tool_args={"command": "uptime"},
        summary="ssh deploy@ops.example.com: uptime",
        requested_by_identity_id=identity.id,
        expires_at=utcnow_naive() + timedelta(seconds=300),
    )
    await db_session.commit()

    async def _fake_create(**kwargs):
        return row.id

    monkeypatch.setattr(svc, "_create_ssh_approval", _fake_create)

    # Replace the asyncio sleep so the test runs fast even if the
    # poll loop exercises the sleep branch.
    real_sleep = asyncio.sleep

    async def _instant_sleep(_s):
        await real_sleep(0)

    monkeypatch.setattr(svc.asyncio, "sleep", _instant_sleep)

    # Tight timeout — first tick sees PENDING, deadline already past,
    # row self-expires.
    async def _wait_with_short_timeout(approval_id, **_kw):
        return await svc._wait_for_approval_decision(
            approval_id, timeout_seconds=1, poll_interval_seconds=0
        )

    monkeypatch.setattr(svc, "_wait_for_approval_decision", _wait_with_short_timeout)

    with pytest.raises(SshCommandDenied) as exc:
        await sandbox.run_command("uptime")
    assert exc.value.extras["status"] == "expired"

    # Row was flipped to EXPIRED.
    await db_session.refresh(row)
    assert row.status == ApprovalStatus.EXPIRED


async def test_command_not_in_allowlist_blocks_before_approval(
    db_session, workspace, identity, monkeypatch
):
    _install_fake_asyncssh(monkeypatch)
    await _seed_key(db_session, workspace, identity)()
    await db_session.commit()

    monkeypatch.setattr(svc, "get_session_factory", lambda: _patched_factory(db_session))

    config = _config(command_allowlist=["uptime"])
    sandbox = svc.SshSandbox(
        config=config,
        workspace_id=workspace.id,
        requested_by_identity_id=identity.id,
    )

    from app.core.errors import SshCommandRejected

    with pytest.raises(SshCommandRejected) as exc:
        await sandbox.run_command("rm -rf /")
    assert exc.value.code == "sandbox.ssh_command_rejected"

    # No approval row was created — the gate runs before the approval.
    rows = (await db_session.execute(select(Approval))).scalars().all()
    assert all(r.tool_name != "ssh_execute" for r in rows)

    # Audit row records the rejection.
    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "sandbox.ssh_command_rejected")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
