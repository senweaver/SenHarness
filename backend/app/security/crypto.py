"""Envelope encryption helpers built on top of a `Keyring` provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from cryptography.fernet import Fernet

from app.security.keyring.base import Keyring


@dataclass(slots=True)
class Sealed:
    ciphertext: bytes        # Fernet(DEK).encrypt(plaintext)
    wrapped_dek: bytes       # KEK.encrypt(DEK)
    kek_version: str         # points at the KEK that wrapped the DEK


def seal(plaintext: bytes, *, keyring: Keyring) -> Sealed:
    dek = Fernet.generate_key()
    ciphertext = Fernet(dek).encrypt(plaintext)
    wrapped, version = keyring.wrap(dek)
    return Sealed(ciphertext=ciphertext, wrapped_dek=wrapped, kek_version=version)


def open_sealed(sealed: Sealed, *, keyring: Keyring) -> bytes:
    dek = keyring.unwrap(sealed.wrapped_dek, sealed.kek_version)
    return Fernet(dek).decrypt(sealed.ciphertext)


def rewrap_for_rotation(sealed: Sealed, *, keyring: Keyring) -> Sealed:
    """Re-wrap an existing DEK with the keyring's current KEK. Ciphertext untouched."""
    dek = keyring.unwrap(sealed.wrapped_dek, sealed.kek_version)
    new_wrapped, new_version = keyring.wrap(dek)
    return Sealed(ciphertext=sealed.ciphertext, wrapped_dek=new_wrapped, kek_version=new_version)


def seal_str(plaintext: str, *, keyring: Keyring) -> Sealed:
    return seal(plaintext.encode(), keyring=keyring)


def open_str(sealed: Sealed, *, keyring: Keyring) -> str:
    return cast(bytes, open_sealed(sealed, keyring=keyring)).decode()
