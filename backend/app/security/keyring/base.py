"""Keyring protocol — pluggable KEK (key-encryption-key) providers.

Envelope encryption model:
  - Every `vault_item.ciphertext` is encrypted with its own **DEK** (Fernet key).
  - The DEK itself is encrypted by the **KEK** from a Keyring provider.
  - `kek_version` on the row picks which KEK to use; rotation rewraps DEKs
    without touching ciphertexts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Keyring(Protocol):
    """Wrap / unwrap a DEK using a symmetric KEK.

    Implementations must return opaque bytes tagged with their own integrity mechanism
    (e.g. Fernet's built-in HMAC, AES-GCM tag, cloud KMS ciphertext blob).
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def current_kek_version(self) -> str: ...

    def wrap(self, dek: bytes) -> tuple[bytes, str]:
        """Encrypt `dek` with the current KEK. Returns `(wrapped_dek, kek_version)`."""

    def unwrap(self, wrapped_dek: bytes, kek_version: str) -> bytes:
        """Decrypt a previously wrapped DEK."""

    def rotate(self) -> str:
        """Produce a new KEK version. Returns the new `kek_version` label.

        Callers are responsible for re-wrapping every outstanding DEK.
        """


class KeyringError(Exception):
    """Raised on any keyring operation failure."""


class KeyringAccessError(KeyringError):
    """Raised when a keyring open / unwrap fails for security reasons.

    M0.8 — wraps the lower-level provider exception so callers (and
    operators reading logs) cannot see the original exception type
    or filesystem path. Pair every raise with a structured log line
    using ``log.warning("keyring.access_failed: provider=%s", ...)``.
    """


def audit_keyring_open(provider_name: str, key_ref: str) -> None:
    """Emit a structured "keyring.opened" event to the standard logger.

    M0.8 — keyring construction happens before an :class:`AsyncSession`
    exists (during process boot, inside :func:`functools.lru_cache`),
    so writing to ``audit_events`` from here would deadlock on the
    DB pool. Instead we surface the event as a single INFO log line
    that operators can ingest into their audit pipeline (Logfire,
    Datadog, Loki, fluentbit). The ``key_ref`` is hashed first so the
    raw filename / vault path / KMS arn never leaks into log files.
    """
    import hashlib
    import logging

    digest = hashlib.sha256((key_ref or "").encode("utf-8")).hexdigest()[:16]
    logging.getLogger("senharness.audit").info(
        "keyring.opened provider=%s key_ref_hash=%s",
        provider_name,
        digest,
    )
