"""Envelope-encrypt channel ``config_json`` secrets at rest.

The Channel-create / update flow accepts plaintext from the frontend
(behind TLS), but the database column shouldn't keep it that way — a
read-only DB dump shouldn't leak bot tokens or AES keys.

We define a tiny helper module instead of inlining the calls so the
two sites that need it (services/channel.py CRUD and services/channels
runtime — when reading the row back to talk to the IM provider) share
exactly the same envelope format. Format:

    "enc:v1:<base64(json({ciphertext, wrapped_dek, kek_version}))>"

The ``enc:v1:`` prefix lets us upgrade to a different scheme later
without ambiguity, and lets ``decrypt_field`` no-op on plaintext rows
written by older code.

Sensitive field names are kept identical to the existing
``mask_config`` set in :mod:`app.services.channel` — both layers must
agree on what counts as a secret.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.security.crypto import Sealed, open_str, seal_str
from app.security.keyring import get_keyring

log = logging.getLogger(__name__)

# Fields that should never live in the DB as plaintext. Mirrors the
# masking set in app.services.channel — keep in sync. Adding a new
# secret-looking config key? Add it here AND in mask_config.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "bot_token",
        "signing_secret",
        "sign_secret",
        "app_secret",
        "client_secret",
        "public_key",  # Discord pubkey is public, but treating uniformly is safer
        "verification_token",
        "secret_token",
        "secret",
        "token",
        "encoding_aes_key",
        "webhook_url",
        "incoming_webhook_url",
        "hmac_secret",
    }
)

_PREFIX = "enc:v1:"


def _is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_field(plaintext: str) -> str:
    """Wrap ``plaintext`` with the current keyring; return the prefixed token.

    Idempotent: passing in an already-encrypted token returns it unchanged
    so callers can blindly run partial updates without double-wrapping.
    """
    if not isinstance(plaintext, str) or not plaintext:
        return plaintext
    if _is_encrypted(plaintext):
        return plaintext
    sealed = seal_str(plaintext, keyring=get_keyring())
    body = json.dumps(
        {
            "c": base64.b64encode(sealed.ciphertext).decode("ascii"),
            "w": base64.b64encode(sealed.wrapped_dek).decode("ascii"),
            "v": sealed.kek_version,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return _PREFIX + base64.b64encode(body).decode("ascii")


def decrypt_field(value: str) -> str:
    """Reverse of :func:`encrypt_field`.

    Returns the plaintext for ``enc:v1:`` tokens; passes everything else
    through untouched (back-compat with rows written before this hook
    landed and with unencrypted "non-secret" fields).
    """
    if not isinstance(value, str) or not _is_encrypted(value):
        return value
    raw = value[len(_PREFIX) :]
    try:
        body = json.loads(base64.b64decode(raw.encode("ascii")).decode("utf-8"))
        sealed = Sealed(
            ciphertext=base64.b64decode(body["c"]),
            wrapped_dek=base64.b64decode(body["w"]),
            kek_version=body["v"],
        )
        return open_str(sealed, keyring=get_keyring())
    except Exception as e:  # pragma: no cover - corrupted ciphertext
        log.error("decrypt_field failed for sealed value: %s", e)
        # Falling back to the raw token is safer than crashing the
        # request — operators see "•••" in the UI and can rotate.
        return ""


def encrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``config`` with every ``SECRET_FIELDS`` value sealed.

    Non-secret keys pass through unchanged. ``None`` / empty string values
    are kept as-is so optional config keys don't acquire ``enc:v1:`` for
    nothing.
    """
    out: dict[str, Any] = {}
    for k, v in (config or {}).items():
        if k in SECRET_FIELDS and isinstance(v, str) and v:
            out[k] = encrypt_field(v)
        else:
            out[k] = v
    return out


def decrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`encrypt_config` — used by the runtime to read
    the live channel configuration before talking to the IM provider.

    Always returns a fresh dict; the caller owns it.
    """
    out: dict[str, Any] = {}
    for k, v in (config or {}).items():
        if k in SECRET_FIELDS and isinstance(v, str):
            out[k] = decrypt_field(v)
        else:
            out[k] = v
    return out
