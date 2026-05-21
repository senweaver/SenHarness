"""Keyring package — pluggable KEK providers + bootstrap helpers.

Usage::

    from app.security.keyring import get_keyring
    kr = get_keyring()
    sealed = seal(b"secret", keyring=kr)
"""

from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache

from app.core.config import settings
from app.security.keyring.base import Keyring, KeyringAccessError, KeyringError
from app.security.keyring.env import EnvKeyring
from app.security.keyring.file_ import FileKeyring
from app.security.keyring.passphrase import PassphraseKeyring

log = logging.getLogger(__name__)


def _reset_keyring_cache() -> None:
    """Utility for admin / rotation flows that swap the backing provider."""

    get_keyring.cache_clear()


# ─── Factory ──────────────────────────────────────────────
@lru_cache
def get_keyring() -> Keyring:
    kind = settings.KEYRING_PROVIDER
    if kind == "env":
        return EnvKeyring()
    if kind == "file":
        return FileKeyring(settings.KEYRING_FILE_PATH)
    if kind == "passphrase":
        return PassphraseKeyring()
    if kind == "aws_kms":  # pragma: no cover — needs boto3 + live CMK
        from app.security.keyring.aws_kms import AwsKmsKeyring

        return AwsKmsKeyring()
    if kind == "gcp_kms":  # pragma: no cover — needs google-cloud-kms + live key
        from app.security.keyring.gcp_kms import GcpKmsKeyring

        return GcpKmsKeyring()
    if kind == "azure_kv":  # pragma: no cover — needs azure-keyvault + live key
        from app.security.keyring.azure_kv import AzureKeyVaultKeyring

        return AzureKeyVaultKeyring()
    if kind == "vault":  # pragma: no cover — needs hvac + live Vault
        from app.security.keyring.vault_ import VaultKeyring

        return VaultKeyring()
    if kind == "hsm":
        raise KeyringError(
            "HSM keyring adapter is not yet bundled. "
            "Extend `app/security/keyring/hsm.py` with a PKCS#11 wrapper."
        )
    raise KeyringError(f"Unsupported keyring provider: {kind!r}")


# ─── Bootstrap ────────────────────────────────────────────
def ensure_master_key_on_startup() -> None:
    """If the Env keyring has no master key, auto-generate, warn, and persist to env.

    Only meaningful in development. Prod should set `SENHARNESS_MASTER_KEY` upfront.
    """
    if settings.KEYRING_PROVIDER != "env":
        return
    if settings.SENHARNESS_MASTER_KEY:
        return

    generated = secrets.token_urlsafe(48)
    # Mutate the cached settings instance in-memory so subsequent calls see the key.
    object.__setattr__(settings, "SENHARNESS_MASTER_KEY", generated)
    os.environ["SENHARNESS_MASTER_KEY"] = generated
    # Drop the cached factory result so EnvKeyring is re-built with the new key.
    get_keyring.cache_clear()

    log.warning(
        "SENHARNESS_MASTER_KEY was empty. Auto-generated an ephemeral key: %s\n"
        "  >>> Persist this value into .env / your secret manager now, otherwise "
        "encrypted Vault items will become unreadable after the next restart.",
        generated,
    )


__all__ = [
    "EnvKeyring",
    "FileKeyring",
    "Keyring",
    "KeyringAccessError",
    "KeyringError",
    "PassphraseKeyring",
    "_reset_keyring_cache",
    "ensure_master_key_on_startup",
    "get_keyring",
]
