"""Pure-function coverage for the SSH sandbox config schema.

Every field-level invariant lives on :class:`SshSandboxConfig`; the
cross-field production-only check lives in :func:`validate_runtime_config`.
Nothing here touches the DB or network so the file runs in the unit
suite without testcontainers.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.core.errors import SandboxKindDisabled, SshConfigInvalid
from app.services.sandbox_ssh import (
    SshSandboxConfig,
    parse_vault_ref,
    validate_runtime_config,
)


def _base_config(**overrides):
    payload = {
        "host": "ops-bastion.example.com",
        "port": 22,
        "user": "deploy",
        "private_key_ref": "vault://workspace/ops_ed25519",
        "known_hosts_pin": ("ops-bastion.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH"),
    }
    payload.update(overrides)
    return payload


def test_default_config_validates():
    config = SshSandboxConfig.model_validate(_base_config())
    assert config.host == "ops-bastion.example.com"
    assert config.port == 22
    assert config.execute is False
    assert config.require_approval is True
    assert config.command_allowlist == []
    assert config.connect_timeout_seconds == 30
    assert config.command_timeout_seconds == 60


def test_plaintext_private_key_rejected():
    with pytest.raises(ValidationError) as exc:
        SshSandboxConfig.model_validate(
            _base_config(private_key_ref="-----BEGIN OPENSSH PRIVATE KEY-----")
        )
    assert any(err["loc"] == ("private_key_ref",) for err in exc.value.errors())


def test_filesystem_path_rejected():
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(
            _base_config(private_key_ref="/etc/ssh/keys/deploy_ed25519")
        )


def test_known_hosts_pin_required():
    payload = _base_config()
    payload.pop("known_hosts_pin")
    with pytest.raises(ValidationError) as exc:
        SshSandboxConfig.model_validate(payload)
    assert any(err["loc"] == ("known_hosts_pin",) for err in exc.value.errors())


def test_known_hosts_pin_empty_rejected():
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(_base_config(known_hosts_pin=""))


def test_port_range_enforced():
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(_base_config(port=0))
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(_base_config(port=70000))


def test_timeout_range_enforced():
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(_base_config(connect_timeout_seconds=0))
    with pytest.raises(ValidationError):
        SshSandboxConfig.model_validate(_base_config(command_timeout_seconds=601))


def test_parse_vault_ref_happy():
    scope, key = parse_vault_ref("vault://workspace/ops_ed25519")
    assert scope == "workspace"
    assert key == "ops_ed25519"


def test_parse_vault_ref_rejects_platform_scope():
    with pytest.raises(SshConfigInvalid) as exc:
        parse_vault_ref("vault://platform/ops_ed25519")
    assert exc.value.code == "sandbox.ssh_config_invalid"
    assert exc.value.extras.get("reason") == "scope_not_allowed"


def test_parse_vault_ref_rejects_missing_key():
    with pytest.raises(SshConfigInvalid):
        parse_vault_ref("vault://workspace/")


def test_parse_vault_ref_rejects_no_prefix():
    with pytest.raises(SshConfigInvalid) as exc:
        parse_vault_ref("plaintext://something")
    assert exc.value.extras.get("reason") == "missing_vault_prefix"


def test_runtime_validation_dev_empty_allowlist_ok(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    from app.core import config as cfg

    cfg.get_settings.cache_clear()
    config = SshSandboxConfig.model_validate(_base_config(execute=True, command_allowlist=[]))
    validate_runtime_config(config)


def test_runtime_validation_production_empty_allowlist_blocked(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from app.core import config as cfg

    cfg.get_settings.cache_clear()
    # Re-bind the cached module-level ``app_settings`` reference because
    # ``validate_runtime_config`` already pulled it in at import time.
    from app.services import sandbox_ssh as svc

    svc.app_settings = cfg.get_settings()
    config = SshSandboxConfig.model_validate(_base_config(execute=True, command_allowlist=[]))
    with pytest.raises(SshConfigInvalid) as exc:
        validate_runtime_config(config)
    assert exc.value.code == "sandbox.ssh_config_invalid"
    assert exc.value.extras.get("reason") == "production_requires_allowlist"
    monkeypatch.setenv("APP_ENV", "development")
    cfg.get_settings.cache_clear()
    svc.app_settings = cfg.get_settings()


def test_runtime_validation_production_with_allowlist_ok(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from app.core import config as cfg

    cfg.get_settings.cache_clear()
    from app.services import sandbox_ssh as svc

    svc.app_settings = cfg.get_settings()
    config = SshSandboxConfig.model_validate(
        _base_config(execute=True, command_allowlist=["ls", "uptime"])
    )
    validate_runtime_config(config)
    monkeypatch.setenv("APP_ENV", "development")
    cfg.get_settings.cache_clear()
    svc.app_settings = cfg.get_settings()


def test_runtime_validation_execute_false_dev_allowlist_optional():
    config = SshSandboxConfig.model_validate(_base_config(execute=False))
    validate_runtime_config(config)


async def test_assert_ssh_backend_allowed_blocks_when_disabled(monkeypatch):
    """Platform default is allow_ssh_backend=False — assertion fails."""
    import uuid as _uuid

    from app.services import sandbox_ssh as svc

    async def _fake_disabled(_db):
        return False

    monkeypatch.setattr(svc, "_ssh_backend_enabled", _fake_disabled)

    async def _fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(svc.audit_svc, "record", _fake_audit)

    with pytest.raises(SandboxKindDisabled) as exc:
        await svc.assert_ssh_backend_allowed(None, workspace_id=_uuid.uuid4())  # type: ignore[arg-type]
    assert exc.value.code == "sandbox.kind_disabled"


async def test_assert_ssh_backend_allowed_passes_when_enabled(monkeypatch):
    import uuid as _uuid

    from app.services import sandbox_ssh as svc

    async def _fake_enabled(_db):
        return True

    monkeypatch.setattr(svc, "_ssh_backend_enabled", _fake_enabled)
    await svc.assert_ssh_backend_allowed(None, workspace_id=_uuid.uuid4())  # type: ignore[arg-type]


# Sanity guard so nothing leaks the production env into the rest of the
# suite: explicit reset honoured by the autouse settings cache fixture.
def teardown_module(_):
    os.environ.pop("APP_ENV", None)
    os.environ.setdefault("APP_ENV", "development")
