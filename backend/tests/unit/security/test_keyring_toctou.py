"""M0.8 — ``FileKeyring._load`` uses fd-first reads + audits open."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.security.keyring.base import KeyringAccessError
from app.security.keyring.file_ import FileKeyring


def _write_keyring(path: Path) -> None:
    version = "file-test"
    key = Fernet.generate_key().decode()
    path.write_text(json.dumps({"current": version, "keys": {version: key}}))
    if not sys.platform.startswith("win"):
        os.chmod(path, 0o600)


def test_keyring_open_emits_audit_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target = tmp_path / "kek.json"
    _write_keyring(target)
    caplog.set_level(logging.INFO, logger="senharness.audit")
    FileKeyring(str(target))
    audit_lines = [r for r in caplog.records if r.name == "senharness.audit"]
    assert any("keyring.opened" in r.message and "provider=file" in r.message for r in audit_lines)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permission semantics")
def test_keyring_refuses_world_readable_file(tmp_path: Path) -> None:
    target = tmp_path / "kek.json"
    _write_keyring(target)
    os.chmod(target, 0o644)
    with pytest.raises(KeyringAccessError):
        FileKeyring(str(target))


def test_keyring_load_uses_fd_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "kek.json"
    _write_keyring(target)
    saw_fstat = {"value": False}
    real_fstat = os.fstat
    real_open = os.open

    def patched_fstat(fd):
        saw_fstat["value"] = True
        return real_fstat(fd)

    def patched_open(path, flags, *args, **kwargs):
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "fstat", patched_fstat)
    monkeypatch.setattr(os, "open", patched_open)
    FileKeyring(str(target))
    assert saw_fstat["value"], "FileKeyring must call os.fstat() on the opened fd"


def test_keyring_malformed_json_wraps_to_keyring_access_error(tmp_path: Path) -> None:
    target = tmp_path / "kek.json"
    target.write_text("not json at all")
    if not sys.platform.startswith("win"):
        os.chmod(target, 0o600)
    with pytest.raises(KeyringAccessError):
        FileKeyring(str(target))
