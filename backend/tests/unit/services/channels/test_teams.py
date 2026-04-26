"""Tests for the Microsoft Teams provider."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.teams import TeamsProvider, _compute_hmac_digest


class TestVerifySignature:
    def test_valid_signature_passes(self):
        provider = TeamsProvider()
        body = b'{"text":"hello"}'
        secret = "teams-secret"
        digest = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")
        provider.verify_signature(
            channel_config={"signing_secret": secret},
            headers={"authorization": f"HMAC {digest}"},
            body=body,
        )

    def test_missing_header_rejected(self):
        provider = TeamsProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"signing_secret": "secret"},
                headers={},
                body=b"{}",
            )
        assert exc.value.code == "teams.missing_signature"

    def test_bad_signature_rejected(self):
        provider = TeamsProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"signing_secret": "secret"},
                headers={"authorization": "HMAC wrong"},
                body=b'{"text":"hello"}',
            )
        assert exc.value.code == "teams.signature_mismatch"

    def test_verify_signatures_false_skips_check(self):
        provider = TeamsProvider()
        provider.verify_signature(
            channel_config={"signing_secret": "secret", "verify_signatures": False},
            headers={},
            body=b"{}",
        )

    def test_compute_digest_supports_base64_encoded_secret(self):
        raw_secret = b"teams-secret"
        encoded_secret = base64.b64encode(raw_secret).decode("utf-8")
        body = b"payload"
        expected = base64.b64encode(
            hmac.new(raw_secret, body, hashlib.sha256).digest()
        ).decode("utf-8")
        assert _compute_hmac_digest(encoded_secret, body) == expected


class TestParseInbound:
    def test_text_message_parsed(self):
        provider = TeamsProvider()
        msg = provider.parse_inbound(
            {
                "text": "  hi teams  ",
                "id": "activity-1",
                "from": {"id": "u1", "name": "Alice"},
                "conversation": {"id": "conv-1"},
            },
            headers={},
        )
        assert msg is not None
        assert msg.user_text == "hi teams"
        assert msg.external_user == "Alice"
        assert msg.thread_key == "conv-1"

    def test_empty_text_ignored(self):
        provider = TeamsProvider()
        msg = provider.parse_inbound({"text": "   "}, headers={})
        assert msg is None
