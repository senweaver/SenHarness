"""Tests for the DingTalk provider's HMAC signature logic.

Signing mistakes in custom-robot integrations are the single most
common cause of "bot is silent after deploy" pain — lock in the
expected behaviour.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.dingtalk import (
    DingTalkProvider,
    _compute_sign,
)


def _valid_headers(secret: str) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    sign = _compute_sign(ts, secret)
    return {"timestamp": ts, "sign": sign}


class TestComputeSign:
    def test_matches_manual_hmac(self):
        """Lock in the exact DingTalk signing formula — any drift
        here means the bot's inbound requests start to fail."""
        ts = "1700000000000"
        secret = "SEC-test-secret"
        expected_payload = f"{ts}\n{secret}".encode("utf-8")
        expected = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                expected_payload,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        assert _compute_sign(ts, secret) == expected


class TestVerifySignature:
    def test_valid_signature_passes(self):
        provider = DingTalkProvider()
        secret = "SEC-test"
        provider.verify_signature(
            channel_config={"sign_secret": secret},
            headers=_valid_headers(secret),
            body=b"{}",
        )

    def test_bad_signature_rejected(self):
        provider = DingTalkProvider()
        headers = _valid_headers("SEC-real")
        headers["sign"] = "WRONGbase64=="
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC-real"},
                headers=headers,
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.signature_mismatch"

    def test_missing_headers_rejected(self):
        provider = DingTalkProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC"},
                headers={},
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.missing_signature_headers"

    def test_stale_timestamp_rejected(self):
        provider = DingTalkProvider()
        secret = "SEC"
        # 2 hours ago — well past the 60-second replay window.
        stale_ts = str(int((time.time() - 7200) * 1000))
        headers = {
            "timestamp": stale_ts,
            "sign": _compute_sign(stale_ts, secret),
        }
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": secret},
                headers=headers,
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.stale_timestamp"

    def test_bad_timestamp_format_rejected(self):
        provider = DingTalkProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC"},
                headers={"timestamp": "not-a-number", "sign": "xxx"},
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.bad_timestamp"

    def test_no_secret_skips_check(self):
        """Channels created before the V2 hardening may not have a
        ``sign_secret`` yet — for those we log a warning (elsewhere)
        and let the request through."""
        provider = DingTalkProvider()
        # Must not raise.
        provider.verify_signature(
            channel_config={},
            headers={},
            body=b"{}",
        )

    def test_verify_signatures_false_opts_out(self):
        """Operator explicitly disabling signature verification
        during dev-tunnel setup must not be blocked."""
        provider = DingTalkProvider()
        provider.verify_signature(
            channel_config={
                "sign_secret": "SEC",
                "verify_signatures": False,
            },
            headers={},
            body=b"{}",
        )


class TestParseInbound:
    def test_text_message_parsed(self):
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {
                "msgtype": "text",
                "text": {"content": "  hello agent  "},
                "senderNick": "Alice",
                "conversationId": "cid-1",
            },
            headers={},
        )
        assert msg is not None
        assert msg.user_text == "hello agent"
        assert msg.external_user == "Alice"
        assert msg.thread_key == "cid-1"

    def test_non_text_ignored(self):
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {"msgtype": "image", "image": {"url": "..."}},
            headers={},
        )
        assert msg is None

    def test_empty_text_ignored(self):
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {"msgtype": "text", "text": {"content": "   "}},
            headers={},
        )
        assert msg is None
