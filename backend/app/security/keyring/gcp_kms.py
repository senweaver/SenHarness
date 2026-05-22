"""GcpKmsKeyring — wrap / unwrap DEKs using Google Cloud KMS.

``settings.GCP_KMS_KEY_NAME`` is the fully-qualified CryptoKey resource name
``projects/*/locations/*/keyRings/*/cryptoKeys/<name>``. The client picks
the primary version automatically on ``encrypt``; ``decrypt`` routes by the
ciphertext headers so we don't need to store an explicit version ourselves.

Optional dependency — install with ``pip install senharness-backend[kms-gcp]``.
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
    from google.cloud.kms import KeyManagementServiceClient
else:  # pragma: no cover
    KeyManagementServiceClient = Any

log = logging.getLogger(__name__)

try:  # pragma: no cover — optional dep
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import kms as _gcp_kms

    _GCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _gcp_kms = None  # type: ignore[assignment]
    GoogleAPIError = Exception  # type: ignore[assignment]
    _GCP_AVAILABLE = False


class GcpKmsKeyring(Keyring):
    def __init__(self) -> None:
        if not _GCP_AVAILABLE:
            raise KeyringError(
                "google-cloud-kms is not installed. Add the optional dependency: "
                "`pip install senharness-backend[kms-gcp]`."
            )
        if not settings.GCP_KMS_KEY_NAME:
            raise KeyringError("GCP_KMS_KEY_NAME is empty; set it to the CryptoKey resource name.")
        self._client: KeyManagementServiceClient = _gcp_kms.KeyManagementServiceClient()
        self._key_name = settings.GCP_KMS_KEY_NAME
        self._version = self._derive_version(self._key_name)
        audit_keyring_open("gcp_kms", self._key_name)

    @property
    def provider_name(self) -> str:
        return "gcp_kms"

    @property
    def current_kek_version(self) -> str:
        return self._version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        try:
            resp = self._client.encrypt(request={"name": self._key_name, "plaintext": dek})
        except GoogleAPIError as e:
            raise KeyringError(f"GCP KMS encrypt failed: {e}") from e
        return bytes(resp.ciphertext), self._version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        if not kek_version.startswith("gcp-"):
            raise KeyringError(f"KEK version {kek_version!r} was not sealed by GcpKmsKeyring.")
        try:
            resp = self._client.decrypt(request={"name": self._key_name, "ciphertext": wrapped_dek})
        except GoogleAPIError as e:
            log.warning("gcp kms unwrap failed: %s", e)
            raise KeyringAccessError("GCP KMS decrypt failed") from e
        return bytes(resp.plaintext)

    def rotate(self) -> str:
        """Creates a new primary CryptoKeyVersion on the configured key."""

        try:
            self._client.create_crypto_key_version(
                request={"parent": self._key_name, "crypto_key_version": {}}
            )
        except GoogleAPIError as e:
            raise KeyringError(f"GCP KMS create_crypto_key_version failed: {e}") from e
        self._version = self._derive_version(self._key_name, rotated=True)
        return self._version

    @staticmethod
    def _derive_version(key_name: str, *, rotated: bool = False) -> str:
        ts = datetime.now(UTC).strftime("%Y%m%d")
        digest = hashlib.sha256(key_name.encode()).hexdigest()[:6]
        tag = "rot" if rotated else ts
        return f"gcp-{digest}-{tag}"
