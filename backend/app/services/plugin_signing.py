"""Plugin signing & load gate (M3.9).

Three responsibilities, sharply scoped:

1. :func:`verify_signature` — pure ed25519 verification. Takes a
   folder ``sha256`` digest (the message), a base64-encoded
   signature (the proof), and a base64-encoded public key (the
   trust root). Returns ``True`` only when the signature was
   produced by the holder of the matching private key over the
   exact digest bytes. Catches every PyNaCl exception so an
   attacker can't crash the load path with a malformed payload.
2. :func:`get_trust_root` — read the platform-wide ed25519 public
   key from ``platform_settings.plugins.signing_root_pubkey``. The
   key is base64 of the 32-byte raw key; we never persist a private
   key here.
3. :func:`evaluate_plugin_for_load` — the gate the loader calls per
   plugin. Returns ``(allowed: bool, reason: str)`` so the loader
   can audit each branch with a stable code. The gate combines:

   * the master ``allow_user_plugins`` switch,
   * the ``allow_unapproved_plugins`` dev-mode escape,
   * the trust root + signature check, and
   * the ``approved_by_platform_admin`` flag on the
     :class:`PluginRegistry` row.

PyNaCl ships as the optional ``[plugin-signing]`` extra. Deployments
that never enable plugins (the shipping default) don't pay the
import cost. When the extra is missing and the operator tries to
verify a signature, :class:`PluginSigningError` surfaces with
``code="pynacl_unavailable"`` and the loader maps that to
``plugin.signature_invalid`` so the audit trail is honest about why
the plugin won't load.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.services.plugin_loader import PluginManifest

log = logging.getLogger(__name__)


class PluginSigningError(Exception):
    """Stable-code error surface for the signing pipeline.

    The ``code`` string is the value the audit row records under
    ``metadata.error_code`` so dashboards can group on a finite set
    rather than the free-form exception message. Codes:

    * ``pynacl_unavailable`` — the optional dependency is missing.
    * ``invalid_pubkey`` — base64 decode failed or wrong byte length.
    * ``invalid_signature`` — base64 decode failed or wrong byte length.
    """

    code: str = "plugin_signing_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


_ED25519_PUBKEY_BYTES = 32
_ED25519_SIG_BYTES = 64


def _b64decode(value: str, *, expected_bytes: int, code: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise PluginSigningError(
            f"base64 decode failed: {exc}", code=code
        ) from exc
    if len(decoded) != expected_bytes:
        raise PluginSigningError(
            f"expected {expected_bytes} bytes after b64 decode, got {len(decoded)}",
            code=code,
        )
    return decoded


def verify_signature(
    folder_sha256: str,
    signature_b64: str,
    trust_root_pubkey_b64: str,
) -> bool:
    """Verify ed25519 signature of ``folder_sha256`` against the trust root.

    The signed message is the lowercase hex digest **encoded as
    UTF-8 bytes** — same string the loader logs in audit metadata,
    so an admin reproducing the signature off-line uses the exact
    bytes they see in the registry row. Returns ``True`` only on a
    cryptographically valid signature; every other path returns
    ``False`` (a malformed input never raises into the loader).
    """
    try:
        import nacl.exceptions
        import nacl.signing
    except ImportError as exc:
        raise PluginSigningError(
            "pynacl is required for plugin signature verification; "
            "install ``pip install \".[plugin-signing]\"`` or set "
            "platform_settings.plugins.allow_unapproved_plugins=True for dev",
            code="pynacl_unavailable",
        ) from exc

    if not folder_sha256 or not signature_b64 or not trust_root_pubkey_b64:
        return False

    try:
        pubkey_bytes = _b64decode(
            trust_root_pubkey_b64,
            expected_bytes=_ED25519_PUBKEY_BYTES,
            code="invalid_pubkey",
        )
        sig_bytes = _b64decode(
            signature_b64,
            expected_bytes=_ED25519_SIG_BYTES,
            code="invalid_signature",
        )
    except PluginSigningError as exc:
        log.warning("plugin signing decode failed (%s)", exc)
        return False

    verify_key = nacl.signing.VerifyKey(pubkey_bytes)
    try:
        verify_key.verify(folder_sha256.encode("utf-8"), sig_bytes)
        return True
    except nacl.exceptions.BadSignatureError:
        return False
    except Exception:
        log.exception("unexpected error during ed25519 verification")
        return False


async def get_trust_root(db: AsyncSession) -> str | None:
    """Return the platform-wide ed25519 public key (base64) or ``None``.

    ``None`` means the operator never pasted a trust root into the
    admin UI; in that case ``evaluate_plugin_for_load`` falls back to
    the ``allow_unapproved_plugins`` switch.
    """
    from app.services import platform_settings as ps_svc

    section = await ps_svc.get_section(
        db, section=ps_svc.PlatformSettingsSection.PLUGINS
    )
    pubkey = getattr(section, "signing_root_pubkey", None)
    if pubkey is None:
        return None
    pubkey = str(pubkey).strip()
    return pubkey or None


async def evaluate_plugin_for_load(
    db: AsyncSession,
    *,
    manifest: PluginManifest,
    sha256: str,
    signature_provided: str | None,
) -> tuple[bool, str]:
    """Decide whether a discovered plugin is allowed to load.

    The reason strings are stable audit codes — the loader writes
    them as ``plugin.<reason>`` audit actions. Branch order matches
    the M3.9 task spec so the audit trail is predictable:

    1. ``disabled`` — master switch off.
    2. ``no_trust_root`` — no pubkey configured AND dev-mode off.
    3. Dev-mode bypass: ``allow_unapproved_plugins=True`` short-
       circuits to ``approved`` regardless of signature/registry
       state. Audit upstream still surfaces this as
       ``plugin.signature_skipped_dev_mode`` so it's visible.
    4. ``signature_missing`` — pubkey is set but the plugin folder
       has no ``plugin.yaml.sig``.
    5. ``signature_invalid`` — verification failed.
    6. ``not_in_registry`` — sha256 has no PluginRegistry row yet
       (admin must scan + approve first).
    7. ``not_approved`` — row exists but
       ``approved_by_platform_admin=False``.
    8. ``approved`` — green light.
    """
    from app.services import platform_settings as ps_svc

    section = await ps_svc.get_section(
        db, section=ps_svc.PlatformSettingsSection.PLUGINS
    )
    allow_user_plugins = bool(getattr(section, "allow_user_plugins", False))
    if not allow_user_plugins:
        return (False, "disabled")

    allow_unapproved = bool(getattr(section, "allow_unapproved_plugins", False))
    trust_root = await get_trust_root(db)

    if not trust_root and not allow_unapproved:
        return (False, "no_trust_root")

    if allow_unapproved:
        return (True, "approved")

    if not signature_provided:
        return (False, "signature_missing")

    try:
        ok = verify_signature(
            folder_sha256=sha256,
            signature_b64=signature_provided,
            trust_root_pubkey_b64=str(trust_root),
        )
    except PluginSigningError as exc:
        log.warning(
            "plugin signing pipeline error (%s) for plugin %s",
            exc.code,
            manifest.name,
        )
        return (False, "signature_invalid")
    if not ok:
        return (False, "signature_invalid")

    from app.repositories.plugin_registry import PluginRegistryRepository

    repo = PluginRegistryRepository(db)
    row = await repo.get_by(
        name=manifest.name, version=manifest.version, sha256=sha256
    )
    if row is None:
        return (False, "not_in_registry")
    if not row.approved_by_platform_admin:
        return (False, "not_approved")

    return (True, "approved")


__all__ = [
    "PluginSigningError",
    "evaluate_plugin_for_load",
    "get_trust_root",
    "verify_signature",
]
