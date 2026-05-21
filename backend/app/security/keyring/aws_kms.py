"""AwsKmsKeyring — wrap / unwrap DEKs using AWS KMS.

AWS KMS handles key version tracking internally: the opaque ``CiphertextBlob``
returned from ``Encrypt`` already encodes which KMS key + version was used,
and any subsequent ``Decrypt`` call routes to the right version automatically.
We still record a local ``kek_version`` string (derived from the KeyId alias
and the call timestamp) so the ``vault_items.kek_version`` column remains
filter-able.

Optional dependency — install with ``pip install senharness-backend[kms-aws]``
(maps to ``boto3 >= 1.35``). Until then, constructing this class raises
``KeyringError`` at instantiation time.
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

if TYPE_CHECKING:  # pragma: no cover — type-check only
    from mypy_boto3_kms import KMSClient
else:  # pragma: no cover
    KMSClient = Any

log = logging.getLogger(__name__)

try:  # pragma: no cover — optional dep
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    _AWS_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    BotoCoreError = ClientError = Exception  # type: ignore[assignment]
    _AWS_AVAILABLE = False


class AwsKmsKeyring(Keyring):
    """Envelope the DEK directly under a KMS Customer Master Key (CMK).

    ``settings.AWS_KMS_KEY_ID`` can be a key ID, ARN, alias name, or alias
    ARN — whatever ``boto3 kms.encrypt(KeyId=...)`` accepts.
    """

    def __init__(self) -> None:
        if not _AWS_AVAILABLE:
            raise KeyringError(
                "boto3 is not installed. Add the optional dependency: "
                "`pip install senharness-backend[kms-aws]`."
            )
        if not settings.AWS_KMS_KEY_ID:
            raise KeyringError(
                "AWS_KMS_KEY_ID is empty; set it to the CMK id / alias / arn."
            )
        region = settings.AWS_REGION or None
        # Let boto3 pick up credentials from the standard chain (env vars,
        # IRSA, instance profile, etc.) — we don't accept explicit creds here
        # because the whole point is the cloud manages KEK material.
        self._client: KMSClient = boto3.client("kms", region_name=region)
        self._key_id = settings.AWS_KMS_KEY_ID
        self._version = self._derive_version(self._key_id)
        audit_keyring_open("aws_kms", self._key_id)

    # ─── Keyring protocol ────────────────────────────────
    @property
    def provider_name(self) -> str:
        return "aws_kms"

    @property
    def current_kek_version(self) -> str:
        return self._version

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        try:
            resp = self._client.encrypt(KeyId=self._key_id, Plaintext=dek)
        except (BotoCoreError, ClientError) as e:
            raise KeyringError(f"AWS KMS Encrypt failed: {e}") from e
        return bytes(resp["CiphertextBlob"]), self._version

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        """KMS decrypts purely from the ciphertext blob — the ``kek_version``
        recorded on the row is advisory. We still verify that the stored
        version starts with ``aws-`` so we don't silently decrypt data sealed
        by a different provider."""

        if not kek_version.startswith("aws-"):
            raise KeyringError(
                f"KEK version {kek_version!r} was not sealed by AwsKmsKeyring."
            )
        try:
            resp = self._client.decrypt(
                CiphertextBlob=wrapped_dek, KeyId=self._key_id
            )
        except (BotoCoreError, ClientError) as e:
            log.warning("aws kms unwrap failed: %s", e)
            raise KeyringAccessError("AWS KMS Decrypt failed") from e
        return bytes(resp["Plaintext"])

    def rotate(self) -> str:
        """KMS rotates key material automatically (yearly or on demand via
        ``UpdateKeyRotationStatus`` / ``ScheduleKeyRotation``). Here we just
        request a fresh symmetric key version — newly sealed items will use
        the new one; old items still decrypt because KMS keeps the history.
        """

        try:
            self._client.rotate_key_on_demand(KeyId=self._key_id)
        except AttributeError:  # pragma: no cover — older boto3
            log.warning(
                "boto3 does not support rotate_key_on_demand; "
                "enable scheduled rotation in the AWS console."
            )
        except (BotoCoreError, ClientError) as e:
            raise KeyringError(f"AWS KMS RotateKeyOnDemand failed: {e}") from e
        self._version = self._derive_version(self._key_id, rotated=True)
        return self._version

    # ─── Helpers ────────────────────────────────────────
    @staticmethod
    def _derive_version(key_id: str, *, rotated: bool = False) -> str:
        ts = datetime.now(UTC).strftime("%Y%m%d")
        digest = hashlib.sha256(key_id.encode()).hexdigest()[:6]
        tag = "rot" if rotated else ts
        return f"aws-{digest}-{tag}"
