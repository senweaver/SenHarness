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
