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

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.security.keyring.base import Keyring, KeyringError


class FileKeyring(Keyring):
    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or settings.KEYRING_FILE_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._bootstrap()
        self._load()

    # ─── File mgmt ───────────────────────────────────────
    def _bootstrap(self) -> None:
        version = "file-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        key = Fernet.generate_key().decode()
        self._path.write_text(
            json.dumps({"current": version, "keys": {version: key}}, indent=2)
        )
        os.chmod(self._path, 0o600)

    def _load(self) -> None:
        data = json.loads(self._path.read_text())
        self._current: str = data["current"]
        self._keys: dict[str, Fernet] = {
            v: Fernet(k.encode()) for v, k in data["keys"].items()
        }
        if self._current not in self._keys:
            raise KeyringError(f"Current KEK {self._current!r} missing from file")

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
