"""AzureKeyVaultKeyring — wrap / unwrap DEKs using Azure Key Vault.

Uses the ``CryptographyClient.wrap_key`` / ``unwrap_key`` primitives with
``KeyWrapAlgorithm.RSA_OAEP_256`` by default — Azure Key Vault supports
symmetric HSM-backed keys too; set ``AZURE_KV_KEY_ALGO`` to override.

``settings.AZURE_KV_URL`` + ``AZURE_KV_KEY_NAME`` identify the key. The key
version embedded in ``kid`` after a ``get_key`` call is what we stamp into
``kek_version`` for traceability. Credential discovery is delegated to
``DefaultAzureCredential`` (env, managed identity, CLI, etc.).

Optional dependency — install with ``pip install senharness-backend[kms-azure]``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.security.keyring.base import (
    Keyring,
    KeyringAccessError,
    KeyringError,
    audit_keyring_open,
)

if TYPE_CHECKING:  # pragma: no cover
    from azure.keyvault.keys.crypto import CryptographyClient
else:  # pragma: no cover
    CryptographyClient = Any

log = logging.getLogger(__name__)

try:  # pragma: no cover — optional dep
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.keys import KeyClient
    from azure.keyvault.keys.crypto import (
        CryptographyClient as _CryptographyClient,
    )
    from azure.keyvault.keys.crypto import KeyWrapAlgorithm

    _AZURE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CryptographyClient = None  # type: ignore[assignment]
    KeyClient = None  # type: ignore[assignment]
    KeyWrapAlgorithm = None  # type: ignore[assignment]
    DefaultAzureCredential = None  # type: ignore[assignment]
    AzureError = Exception  # type: ignore[assignment]
    _AZURE_AVAILABLE = False


class AzureKeyVaultKeyring(Keyring):
    def __init__(self) -> None:
        if not _AZURE_AVAILABLE:
            raise KeyringError(
                "azure-keyvault-keys is not installed. Add the optional dependency: "
                "`pip install senharness-backend[kms-azure]`."
            )
        if not settings.AZURE_KV_URL or not settings.AZURE_KV_KEY_NAME:
            raise KeyringError("AZURE_KV_URL and AZURE_KV_KEY_NAME must both be set.")
        credential = DefaultAzureCredential()
        self._kv_url = settings.AZURE_KV_URL
        self._key_name = settings.AZURE_KV_KEY_NAME
        # Fetch the current key so we pin its exact version. Subsequent
        # ``wrap_key`` calls on this client use that same version, and
        # ``unwrap_key`` routes by the version embedded in ``kid``.
        self._key_client = KeyClient(vault_url=self._kv_url, credential=credential)
        self._cached_kid, self._cached_version = self._fetch_current(credential)
        self._crypto: CryptographyClient = _CryptographyClient(
            self._cached_kid, credential=credential
        )
        # RSA-OAEP-256 is the only wrap algo Azure KV guarantees for software-
        # backed keys; HSM-backed keys add AES-KW but we default to the
        # lowest-common-denominator to keep provisioning simple.
        self._algo = KeyWrapAlgorithm.RSA_OAEP_256
        self._credential = credential
        audit_keyring_open("azure_kv", self._cached_kid)

    def _fetch_current(self, credential: Any) -> tuple[str, str]:
        _ = credential  # reserved: future per-call credential swap
        try:
            key = self._key_client.get_key(self._key_name)
        except AzureError as e:
            raise KeyringError(f"Azure Key Vault get_key failed: {e}") from e
        kid = key.id or f"{self._kv_url}/keys/{self._key_name}"
        version = kid.rsplit("/", 1)[-1] if "/" in kid else "current"
        return kid, f"azure-{version[:12]}"

    @property
    def provider_name(self) -> str:
        return "azure_kv"

    @property
    def current_kek_version(self) -> str:
        return self._cached_version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        try:
            resp = self._crypto.wrap_key(self._algo, dek)
        except AzureError as e:
            raise KeyringError(f"Azure KV wrap_key failed: {e}") from e
        return bytes(resp.encrypted_key), self._cached_version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        if not kek_version.startswith("azure-"):
            raise KeyringError(
                f"KEK version {kek_version!r} was not sealed by AzureKeyVaultKeyring."
            )
        try:
            resp = self._crypto.unwrap_key(self._algo, wrapped_dek)
        except AzureError as e:
            log.warning("azure kv unwrap failed: %s", e)
            raise KeyringAccessError("Azure KV unwrap_key failed") from e
        return bytes(resp.key)

    def rotate(self) -> str:
        """Create a new key version on Key Vault. The updated resource's
        ``kid`` becomes the new cached kid + version for subsequent wraps."""

        try:
            # Creating an RSA key with the same name rotates it (new version).
            new_key = self._key_client.create_rsa_key(self._key_name, size=3072)
        except AzureError as e:
            raise KeyringError(f"Azure KV rotate failed: {e}") from e
        new_kid = new_key.id or f"{self._kv_url}/keys/{self._key_name}"
        version = new_kid.rsplit("/", 1)[-1] if "/" in new_kid else "current"
        self._cached_kid = new_kid
        self._cached_version = f"azure-{version[:12]}-{datetime.now(UTC):%Y%m%d}"
        self._crypto = _CryptographyClient(new_kid, credential=self._credential)
        return self._cached_version

    @staticmethod
    def _hash_kid(kid: str) -> str:
        return hashlib.sha256(kid.encode()).hexdigest()[:8]
