"""Plugin signing unit tests (M3.9).

Cover the three pieces independently:

1. :func:`verify_signature` — happy path, wrong-key reject, malformed
   sig reject, bytes-length guard.
2. :func:`evaluate_plugin_for_load` — every gate branch returns the
   stable reason code the loader audits on.
3. ``allow_unapproved_plugins=True`` short-circuits the signature
   pipeline for dev mode.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from app.services import plugin_signing
from app.services.plugin_loader import PluginManifest
from app.services.plugin_signing import (
    PluginSigningError,
    evaluate_plugin_for_load,
    verify_signature,
)

pynacl = pytest.importorskip("nacl.signing")


def _signing_pair() -> tuple[str, str]:
    """Generate an ed25519 keypair, return ``(pubkey_b64, signing_key_b64)``."""
    sk = pynacl.SigningKey.generate()
    return (
        base64.b64encode(bytes(sk.verify_key)).decode("ascii"),
        base64.b64encode(bytes(sk)).decode("ascii"),
    )


def _sign_digest(signing_key_b64: str, digest_hex: str) -> str:
    sk = pynacl.SigningKey(base64.b64decode(signing_key_b64))
    sig = sk.sign(digest_hex.encode("utf-8")).signature
    return base64.b64encode(sig).decode("ascii")


def _manifest() -> PluginManifest:
    return PluginManifest(
        name="alpha",
        version="0.0.1",
        description="test plugin",
        capability_scopes=("pre_tool_call",),
        entry_module="alpha.entry",
    )


# ── verify_signature ─────────────────────────────────────────
def test_verify_signature_happy_path() -> None:
    pubkey, sk = _signing_pair()
    digest = "deadbeef" * 8
    sig = _sign_digest(sk, digest)
    assert verify_signature(digest, sig, pubkey) is True


def test_verify_signature_wrong_key_rejects() -> None:
    pub_a, sk_a = _signing_pair()
    pub_b, _ = _signing_pair()
    digest = "deadbeef" * 8
    sig = _sign_digest(sk_a, digest)
    assert verify_signature(digest, sig, pub_b) is False


def test_verify_signature_corrupted_sig_rejects() -> None:
    pubkey, sk = _signing_pair()
    digest = "deadbeef" * 8
    sig = _sign_digest(sk, digest)
    # Flip a byte in the middle of the b64 payload — still decodes
    # to 64 bytes, but ed25519 rejects.
    raw = bytearray(base64.b64decode(sig))
    raw[10] ^= 0xFF
    bad_sig = base64.b64encode(bytes(raw)).decode("ascii")
    assert verify_signature(digest, bad_sig, pubkey) is False


def test_verify_signature_malformed_pubkey_returns_false() -> None:
    digest = "deadbeef" * 8
    sig = base64.b64encode(b"\x00" * 64).decode("ascii")
    assert verify_signature(digest, sig, "not_base64!!!") is False


def test_verify_signature_short_pubkey_returns_false() -> None:
    digest = "deadbeef" * 8
    sig = base64.b64encode(b"\x00" * 64).decode("ascii")
    pubkey = base64.b64encode(b"\x00" * 16).decode("ascii")
    assert verify_signature(digest, sig, pubkey) is False


def test_verify_signature_empty_inputs_return_false() -> None:
    assert verify_signature("", "", "") is False


# ── evaluate_plugin_for_load ─────────────────────────────────
class _StubSettings:
    def __init__(
        self,
        *,
        allow_user_plugins: bool = True,
        allow_unapproved_plugins: bool = False,
        signing_root_pubkey: str | None = None,
    ) -> None:
        self.allow_user_plugins = allow_user_plugins
        self.allow_unapproved_plugins = allow_unapproved_plugins
        self.signing_root_pubkey = signing_root_pubkey


class _StubRegistryRow:
    def __init__(self, *, approved: bool = False) -> None:
        self.approved_by_platform_admin = approved


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: _StubSettings) -> None:
    """Wire ``platform_settings.get_section`` to our stub.

    We bypass the real DB read so the unit tests don't need pg.
    """

    async def _get_section(_db: Any, *, section: Any) -> _StubSettings:
        return settings

    import app.services.platform_settings as ps

    monkeypatch.setattr(ps, "get_section", _get_section)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, row: _StubRegistryRow | None) -> None:
    """Wire :class:`PluginRegistryRepository.get_by` to return ``row``."""

    class _StubRepo:
        def __init__(self, _db: Any) -> None:
            pass

        async def get_by_sha(self, **_kwargs: Any) -> _StubRegistryRow | None:
            return row

        async def get_by(self, **_kwargs: Any) -> _StubRegistryRow | None:
            return row

    import app.repositories.plugin_registry as repo_mod

    monkeypatch.setattr(repo_mod, "PluginRegistryRepository", _StubRepo)


def test_evaluate_disabled_returns_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _StubSettings(allow_user_plugins=False))
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None, manifest=_manifest(), sha256="x" * 64, signature_provided=None
        )
    )
    assert allowed is False
    assert reason == "disabled"


def test_evaluate_no_trust_root_when_pubkey_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(
        monkeypatch,
        _StubSettings(
            allow_user_plugins=True,
            allow_unapproved_plugins=False,
            signing_root_pubkey=None,
        ),
    )
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None, manifest=_manifest(), sha256="x" * 64, signature_provided="sig"
        )
    )
    assert allowed is False
    assert reason == "no_trust_root"


def test_evaluate_signature_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubkey, _ = _signing_pair()
    _patch_settings(
        monkeypatch,
        _StubSettings(
            allow_user_plugins=True,
            allow_unapproved_plugins=False,
            signing_root_pubkey=pubkey,
        ),
    )
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None, manifest=_manifest(), sha256="x" * 64, signature_provided=None
        )
    )
    assert allowed is False
    assert reason == "signature_missing"


def test_evaluate_signature_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubkey_a, _ = _signing_pair()
    _, sk_b = _signing_pair()
    digest = "f" * 64
    sig_b = _sign_digest(sk_b, digest)
    _patch_settings(
        monkeypatch,
        _StubSettings(
            allow_user_plugins=True,
            signing_root_pubkey=pubkey_a,
        ),
    )
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None,
            manifest=_manifest(),
            sha256=digest,
            signature_provided=sig_b,
        )
    )
    assert allowed is False
    assert reason == "signature_invalid"


def test_evaluate_not_in_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    pubkey, sk = _signing_pair()
    digest = "a" * 64
    sig = _sign_digest(sk, digest)
    _patch_settings(
        monkeypatch,
        _StubSettings(allow_user_plugins=True, signing_root_pubkey=pubkey),
    )
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None,
            manifest=_manifest(),
            sha256=digest,
            signature_provided=sig,
        )
    )
    assert allowed is False
    assert reason == "not_in_registry"


def test_evaluate_not_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    pubkey, sk = _signing_pair()
    digest = "b" * 64
    sig = _sign_digest(sk, digest)
    _patch_settings(
        monkeypatch,
        _StubSettings(allow_user_plugins=True, signing_root_pubkey=pubkey),
    )
    _patch_registry(monkeypatch, _StubRegistryRow(approved=False))
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None,
            manifest=_manifest(),
            sha256=digest,
            signature_provided=sig,
        )
    )
    assert allowed is False
    assert reason == "not_approved"


def test_evaluate_approved_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    pubkey, sk = _signing_pair()
    digest = "c" * 64
    sig = _sign_digest(sk, digest)
    _patch_settings(
        monkeypatch,
        _StubSettings(allow_user_plugins=True, signing_root_pubkey=pubkey),
    )
    _patch_registry(monkeypatch, _StubRegistryRow(approved=True))
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None,
            manifest=_manifest(),
            sha256=digest,
            signature_provided=sig,
        )
    )
    assert allowed is True
    assert reason == "approved"


def test_evaluate_dev_mode_skips_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``allow_unapproved_plugins=True`` short-circuits the entire
    signature + approval pipeline. Dev-only flag.
    """
    _patch_settings(
        monkeypatch,
        _StubSettings(
            allow_user_plugins=True,
            allow_unapproved_plugins=True,
            signing_root_pubkey=None,
        ),
    )
    _patch_registry(monkeypatch, None)
    allowed, reason = asyncio.run(
        evaluate_plugin_for_load(
            None,
            manifest=_manifest(),
            sha256="d" * 64,
            signature_provided=None,
        )
    )
    assert allowed is True
    assert reason == "approved"


def test_plugin_signing_error_carries_code() -> None:
    err = PluginSigningError("boom", code="invalid_pubkey")
    assert err.code == "invalid_pubkey"
    assert "boom" in str(err)


def test_plugin_signing_module_exports() -> None:
    assert hasattr(plugin_signing, "verify_signature")
    assert hasattr(plugin_signing, "evaluate_plugin_for_load")
    assert hasattr(plugin_signing, "get_trust_root")
