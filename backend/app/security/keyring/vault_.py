"""VaultKeyring — wrap / unwrap DEKs using HashiCorp Vault Transit.

Transit stores and rotates KEKs for us; we only ever send the raw DEK to
``transit/encrypt/<key>`` and receive back an opaque ciphertext string of the
form ``vault:v<N>:<base64>``. Rotation is a single POST to
``transit/keys/<key>/rotate`` and the new version becomes the default for
subsequent encrypts. Old ciphertexts decrypt automatically via the ``vN``
prefix.

Optional dependency — install with ``pip install senharness-backend[kms-vault]``
(maps to ``hvac >= 2.3``).
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.security.keyring.base import (
    Keyring,
    KeyringAccessError,
    KeyringError,
    audit_keyring_open,
)

if TYPE_CHECKING:  # pragma: no cover
    from hvac import Client as _HVac
else:  # pragma: no cover
    _HVac = Any

log = logging.getLogger(__name__)

try:  # pragma: no cover — optional dep
    import hvac as _hvac_mod
    from hvac.exceptions import VaultError

    _VAULT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _hvac_mod = None  # type: ignore[assignment]
    VaultError = Exception  # type: ignore[assignment]
    _VAULT_AVAILABLE = False


class VaultKeyring(Keyring):
    def __init__(self) -> None:
        if not _VAULT_AVAILABLE:
            raise KeyringError(
                "hvac is not installed. Add the optional dependency: "
                "`pip install senharness-backend[kms-vault]`."
            )
        if not settings.VAULT_ADDR or not settings.VAULT_TRANSIT_KEY:
            raise KeyringError("VAULT_ADDR and VAULT_TRANSIT_KEY must both be set.")
        token = _get_vault_token()
        self._client: _HVac = _hvac_mod.Client(url=settings.VAULT_ADDR, token=token)
        if not self._client.is_authenticated():
            raise KeyringError(
                "Vault client failed to authenticate. Check VAULT_TOKEN / "
                "app role / kubernetes auth."
            )
        self._transit_key = settings.VAULT_TRANSIT_KEY
        self._version = self._derive_version()
        audit_keyring_open("vault", self._transit_key)

    # ─── Keyring protocol ────────────────────────────────
    @property
    def provider_name(self) -> str:
        return "vault"

    @property
    def current_kek_version(self) -> str:
        return self._version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        plaintext_b64 = base64.b64encode(dek).decode("ascii")
        try:
            resp = self._client.secrets.transit.encrypt_data(
                name=self._transit_key, plaintext=plaintext_b64
            )
        except VaultError as e:
            raise KeyringError(f"Vault Transit encrypt failed: {e}") from e
        ciphertext = resp["data"]["ciphertext"]
        # ``ciphertext`` is a string like ``vault:v3:xxxx``. We store it as
        # UTF-8 bytes so it survives the binary-safe ``wrapped_dek`` column.
        return ciphertext.encode("ascii"), self._version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        if not kek_version.startswith("vault-"):
            raise KeyringError(f"KEK version {kek_version!r} was not sealed by VaultKeyring.")
        try:
            resp = self._client.secrets.transit.decrypt_data(
                name=self._transit_key,
                ciphertext=wrapped_dek.decode("ascii"),
            )
        except VaultError as e:
            log.warning("vault keyring unwrap failed: %s", e)
            raise KeyringAccessError("Vault Transit decrypt failed") from e
        return base64.b64decode(resp["data"]["plaintext"])

    def rotate(self) -> str:
        try:
            self._client.secrets.transit.rotate_key(name=self._transit_key)
        except VaultError as e:
            raise KeyringError(f"Vault Transit rotate failed: {e}") from e
        self._version = self._derive_version(rotated=True)
        return self._version

    # ─── Helpers ────────────────────────────────────────
    def _derive_version(self, *, rotated: bool = False) -> str:
        try:
            resp = self._client.secrets.transit.read_key(name=self._transit_key)
            latest = int(resp["data"]["latest_version"])
        except (VaultError, KeyError, ValueError, TypeError):  # pragma: no cover
            latest = 0
        suffix = "rot" if rotated else "v"
        return f"vault-{self._transit_key}-{suffix}{latest}"


def _get_vault_token() -> str:
    """Minimal token resolution: env var first, then the standard
    ``~/.vault-token`` file. Production deployments will set ``VAULT_TOKEN``
    via a secret manager / workload identity."""

    import os
    from pathlib import Path

    token = os.environ.get("VAULT_TOKEN", "").strip()
    if token:
        return token
    tf = Path.home() / ".vault-token"
    if tf.exists():
        return tf.read_text().strip()
    raise KeyringError(
        "No Vault token available; set VAULT_TOKEN env var or provision ~/.vault-token before boot."
    )
