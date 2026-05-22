"""PassphraseKeyring — Vault-style startup unseal.

On process start an operator supplies a passphrase via stdin / SENHARNESS_PASSPHRASE;
KEK is derived via argon2id and kept in memory only.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
from threading import Lock

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet, InvalidToken

from app.security.keyring.base import (
    Keyring,
    KeyringAccessError,
    KeyringError,
    audit_keyring_open,
)


class PassphraseKeyring(Keyring):
    _singleton_lock = Lock()

    def __init__(self, passphrase: str | None = None, *, salt: bytes | None = None) -> None:
        passphrase = (
            passphrase or os.environ.get("SENHARNESS_PASSPHRASE") or getpass.getpass("Passphrase: ")
        )
        if not passphrase:
            raise KeyringError("Passphrase required for PassphraseKeyring")
        salt = salt or b"senharness-keyring-salt-v1"
        derived = hash_secret_raw(
            secret=passphrase.encode(),
            salt=salt,
            time_cost=3,
            memory_cost=64 * 1024,
            parallelism=2,
            hash_len=32,
            type=Type.ID,
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))
        self._version = "pw-" + hashlib.sha256(derived).hexdigest()[:8]
        audit_keyring_open("passphrase", self._version)

    @property
    def provider_name(self) -> str:
        return "passphrase"

    @property
    def current_kek_version(self) -> str:
        return self._version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        return self._fernet.encrypt(dek), self._version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        if kek_version != self._version:
            raise KeyringError(
                f"KEK version mismatch; current={self._version} stored={kek_version}. "
                "Re-unseal with previous passphrase, then rotate."
            )
        try:
            return self._fernet.decrypt(wrapped_dek)
        except InvalidToken as e:
            raise KeyringAccessError("Failed to unwrap DEK") from e

    def rotate(self) -> str:
        raise KeyringError(
            "PassphraseKeyring rotation requires unsealing with the NEW passphrase; "
            "use the admin CLI to perform that operation."
        )
