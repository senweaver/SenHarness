"""EnvKeyring — KEK loaded from `SENHARNESS_MASTER_KEY` env var.

The master value is a Fernet key (32 raw bytes, base64-urlsafe encoded).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.security.keyring.base import (
    Keyring,
    KeyringAccessError,
    KeyringError,
    audit_keyring_open,
)


def _derive_fernet_key(raw: str) -> bytes:
    """Accept any string by deriving a 32-byte Fernet key via SHA-256, base64 encoded."""
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class EnvKeyring(Keyring):
    """Single-KEK keyring — `SENHARNESS_MASTER_KEY` from env."""

    def __init__(self) -> None:
        raw = settings.SENHARNESS_MASTER_KEY
        if not raw:
            raise KeyringError(
                "SENHARNESS_MASTER_KEY is empty; cannot initialize EnvKeyring. "
                "Let the app auto-generate one on first boot, or set it in .env."
            )
        self._fernet = Fernet(_derive_fernet_key(raw))
        # Version == first 8 hex chars of sha256(raw); changes only on key rotation.
        self._version = "env-" + hashlib.sha256(raw.encode()).hexdigest()[:8]
        audit_keyring_open("env", self._version)

    @property
    def provider_name(self) -> str:
        return "env"

    @property
    def current_kek_version(self) -> str:
        return self._version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        return self._fernet.encrypt(dek), self._version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        if kek_version != self._version:
            # Future: lookup retired KEKs by version from a secondary store.
            raise KeyringError(
                f"KEK version mismatch: stored={kek_version} current={self._version}. "
                "Rotate / re-wrap via `make rotate-kek` after updating the env var."
            )
        try:
            return self._fernet.decrypt(wrapped_dek)
        except InvalidToken as e:
            raise KeyringAccessError("Failed to unwrap DEK") from e

    def rotate(self) -> str:
        raise KeyringError(
            "EnvKeyring rotation is operator-driven: update SENHARNESS_MASTER_KEY "
            "in your secret store, then run `make rotate-kek` to re-wrap DEKs."
        )
