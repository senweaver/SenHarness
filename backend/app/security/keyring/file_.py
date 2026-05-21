"""FileKeyring — KEK persisted to a local JWK-ish JSON file.

File format::

    {
      "current": "file-20260101",
      "keys": {
        "file-20260101": "<base64-fernet-key>",
        "file-20251001": "<previous-key-for-unwrap>"
      }
    }
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.security.keyring.base import (
    Keyring,
    KeyringAccessError,
    KeyringError,
    audit_keyring_open,
)

log = logging.getLogger(__name__)


class FileKeyring(Keyring):
    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or settings.KEYRING_FILE_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._bootstrap()
        self._load()
        audit_keyring_open("file", str(self._path))

    # ─── File mgmt ───────────────────────────────────────
    def _bootstrap(self) -> None:
        version = "file-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        key = Fernet.generate_key().decode()
        self._path.write_text(
            json.dumps({"current": version, "keys": {version: key}}, indent=2)
        )
        # Windows POSIX-mode write is best-effort — NTFS ACLs gate access
        # there instead.
        with contextlib.suppress(OSError):  # pragma: no cover
            os.chmod(self._path, 0o600)

    def _load(self) -> None:
        # M0.8 — fd-first read closes the TOCTOU window between
        # ``stat()`` and ``open()`` that the previous "stat → open"
        # pattern carried. We open() the descriptor, fstat() the
        # *opened* fd, and only then hand the handle to a Python
        # file object. Any swap or symlink between checks now trips
        # the ``KeyringAccessError`` instead of silently sealing keys
        # under attacker-controlled mode bits.
        fd = os.open(str(self._path), os.O_RDONLY)
        owns_fd = True
        try:
            st = os.fstat(fd)
            self._enforce_permissions(st)
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                owns_fd = False
                raw = fh.read()
        finally:
            if owns_fd:
                with contextlib.suppress(OSError):  # pragma: no cover
                    os.close(fd)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeyringAccessError("keyring file is not valid JSON") from exc

        try:
            self._current: str = data["current"]
            self._keys: dict[str, Fernet] = {
                v: Fernet(k.encode()) for v, k in data["keys"].items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyringAccessError("keyring file structure is malformed") from exc

        if self._current not in self._keys:
            raise KeyringError(f"Current KEK {self._current!r} missing from file")

    @staticmethod
    def _enforce_permissions(st: os.stat_result) -> None:
        """Reject world / group readable keyring files.

        On POSIX we hard-fail when the mode is anything other than
        ``0o600``. On Windows ``stat.st_mode`` does not carry POSIX
        bits in any meaningful sense (NTFS ACLs gate access instead),
        so we degrade to a single warning rather than refusing to
        boot — operators who want the same hard-fail discipline can
        configure NTFS ACLs externally and the host-level ACL will
        still gate the open() above.
        """
        if sys.platform.startswith("win"):
            extra = stat.S_IRWXG | stat.S_IRWXO
            if st.st_mode & extra:
                log.warning(
                    "keyring file mode appears group/world-readable; "
                    "tighten NTFS ACLs on this file"
                )
            return
        mode_bits = st.st_mode & 0o777
        if mode_bits != 0o600:
            raise KeyringAccessError(
                f"keyring file permissions must be 600 (got 0o{mode_bits:o})"
            )

    def _save(self) -> None:
        out = {
            "current": self._current,
            "keys": {v: f._signing_key.hex() + f._encryption_key.hex() if False else None for v, f in self._keys.items()},
        }
        # Above line guarded by `if False` — we must never export Fernet internals.
        # Instead we keep raw keys in a shadow dict set at bootstrap/rotate.
        raise KeyringError("FileKeyring._save() requires raw-key tracking; use rotate()")

    # ─── Protocol ────────────────────────────────────────
    @property
    def provider_name(self) -> str:
        return "file"

    @property
    def current_kek_version(self) -> str:
        return self._current

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        return self._keys[self._current].encrypt(dek), self._current

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        fernet = self._keys.get(kek_version)
        if fernet is None:
            raise KeyringError(f"KEK version {kek_version!r} not found in file")
        try:
            return fernet.decrypt(wrapped_dek)
        except InvalidToken as e:
            raise KeyringError("Failed to unwrap DEK (InvalidToken)") from e

    def rotate(self) -> str:
        new_version = "file-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
        new_key = Fernet.generate_key()
        data = json.loads(self._path.read_text())
        data["keys"][new_version] = new_key.decode()
        data["current"] = new_version
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
        # Re-load into memory
        self._keys[new_version] = Fernet(new_key)
        self._current = new_version
        return new_version
