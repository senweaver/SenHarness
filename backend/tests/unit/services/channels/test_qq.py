"""Tests for the QQ Bot provider — V2 Ed25519 signing + parse_inbound."""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.channels.base import SignatureInvalid
from app.services.channels.qq import (
    QQBotProvider,
    _ed25519_seed_from_secret,
    _sign_plain_token,
    _verify_v2_signature,
)


class TestMetadata:
    def test_required_fields(self):
        meta = QQBotProvider.metadata()
        assert meta.kind == "qq"
        assert {"app_id", "app_secret"} <= set(meta.required_config_fields)
        assert meta.supports_outbound is True

    def test_default_mode_stream(self):
        meta = QQBotProvider.metadata()
        assert meta.default_mode == "stream"
        assert "stream" in meta.supported_modes


class TestEd25519Math:
    def test_seed_repeats_to_32_bytes(self):
        # The published rule: repeat secret bytes until len ≥ 32 then trim.
        # A 7-byte secret should land at 4*7=28 then 35; we trim to 32.
        seed = _ed25519_seed_from_secret("abc1234")
        assert len(seed) == 32
        # Sanity check: same input ⇒ same seed.
        assert seed == _ed25519_seed_from_secret("abc1234")

    def test_sign_and_verify_roundtrip(self):
        """Sign with the helper, verify with a from-scratch keypair."""
        secret = "test_secret_12345678901234567890"
        plain = "plain-token"
        ts = "1700000000"
        sig_hex = _sign_plain_token(plain, ts, secret)

        # Re-derive the public key the same way the verify helper does.
        sk = Ed25519PrivateKey.from_private_bytes(_ed25519_seed_from_secret(secret))
        pk = sk.public_key()
        msg = (ts + plain).encode("utf-8")
        # cryptography raises InvalidSignature if it fails — pass means verified.
        pk.verify(bytes.fromhex(sig_hex), msg)

    def test_handshake_response_signs_correctly(self):
        provider = QQBotProvider()
        secret = "xyzpdq" * 6
        resp = provider.handshake_response(
            {
                "op": 13,
                "d": {"plain_token": "tok", "event_ts": "1700"},
                "_app_secret": secret,
            }
        )
        assert resp is not None
        assert resp["plain_token"] == "tok"
        # Signature shape: 64-byte (128 hex chars) Ed25519 signature.
        assert len(resp["signature"]) == 128
        # And the same input should reproduce it.
        again = _sign_plain_token("tok", "1700", secret)
        assert again == resp["signature"]


class TestVerifyV2Signature:
    def _make_headers(self, secret: str, body: bytes, ts: str = "1700"):
        sk = Ed25519PrivateKey.from_private_bytes(_ed25519_seed_from_secret(secret))
        sig = sk.sign(ts.encode() + body).hex()
        return {
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": ts,
        }

    def test_valid_passes(self):
        secret = "qq_test_secret_aaaa"
        body = json.dumps({"op": 0, "t": "READY", "d": {}}).encode()
        headers = self._make_headers(secret, body)
        _verify_v2_signature(secret=secret, headers=headers, body=body)  # no raise

    def test_bad_sig_rejected(self):
        secret = "qq_test_secret_aaaa"
        body = b"{}"
        headers = self._make_headers(secret, body)
        headers["X-Signature-Ed25519"] = "00" * 64  # wrong sig, valid hex shape
        with pytest.raises(SignatureInvalid) as exc:
            _verify_v2_signature(secret=secret, headers=headers, body=body)
        assert exc.value.code == "qq.signature_mismatch"

    def test_missing_headers_rejected(self):
        with pytest.raises(SignatureInvalid) as exc:
            _verify_v2_signature(secret="x", headers={}, body=b"")
        assert exc.value.code == "qq.missing_signature_headers"

    def test_provider_skip_when_no_secret(self):
        # Back-compat: missing secret → don't crash, just allow.
        QQBotProvider().verify_signature(
            channel_config={},
            headers={},
            body=b"",
        )


class TestParseInbound:
    def test_at_message_create(self):
        payload = {
            "op": 0,
            "t": "AT_MESSAGE_CREATE",
            "d": {
                "content": "  hi bot  ",
                "channel_id": "ch-1",
                "guild_id": "g-1",
                "author": {"id": "u-1"},
            },
        }
        msg = QQBotProvider().parse_inbound(payload, headers={})
        assert msg is not None
        assert msg.user_text == "hi bot"
        assert msg.thread_key.startswith("qq_guild:")

    def test_group_at_message_create(self):
        payload = {
            "op": 0,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {
                "content": "yo",
                "group_openid": "grp-1",
                "author": {"member_openid": "m-1"},
            },
        }
        msg = QQBotProvider().parse_inbound(payload, headers={})
        assert msg is not None
        assert msg.thread_key == "qq_group:grp-1"

    def test_op_13_returns_none(self):
        # Handshake op should be intercepted by handshake_response, not by
        # parse_inbound.
        msg = QQBotProvider().parse_inbound({"op": 13, "d": {}}, headers={})
        assert msg is None

    def test_other_event_ignored(self):
        msg = QQBotProvider().parse_inbound(
            {"op": 0, "t": "READY", "d": {}}, headers={}
        )
        assert msg is None
