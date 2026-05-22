"""Tests for the WeChat (iLink Bot) provider."""

from __future__ import annotations

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.wechat import WeChatProvider


class TestMetadata:
    def test_no_globally_required_fields(self):
        """Stream is the primary path: the operator scans a QR and the
        backend writes ``bot_token`` back into ``config_json`` itself,
        so the create form has nothing to demand up front. The relay
        webhook fallback still asks for ``bot_token`` via per-mode
        overrides (see ``test_required_field_per_mode``)."""
        meta = WeChatProvider.metadata()
        assert meta.required_config_fields == ()
        assert "bot_token" in meta.optional_config_fields
        assert "iLink" in meta.display_name
        assert meta.supports_outbound is True

    def test_required_field_per_mode(self):
        meta = WeChatProvider.metadata()
        assert meta.mode_required_fields == {
            "stream": (),
            "webhook": ("bot_token",),
        }

    def test_default_mode_stream(self):
        meta = WeChatProvider.metadata()
        assert meta.default_mode == "stream"
        assert set(meta.supported_modes) == {"webhook", "stream"}

    def test_stream_available_no_extra_required(self):
        # iLink long-poll is built on httpx — already a base dep.
        meta = WeChatProvider.metadata()
        assert meta.stream_requires_extra is None
        assert WeChatProvider.stream_available() is True


class TestVerifySignature:
    def test_relay_token_match(self):
        WeChatProvider().verify_signature(
            channel_config={"relay_token": "rt"},
            headers={"x-relay-token": "rt"},
            body=b"{}",
        )

    def test_relay_token_mismatch(self):
        with pytest.raises(SignatureInvalid) as exc:
            WeChatProvider().verify_signature(
                channel_config={"relay_token": "rt"},
                headers={"x-relay-token": "wrong"},
                body=b"{}",
            )
        assert exc.value.code == "wechat.relay_token_mismatch"

    def test_no_relay_token_skips(self):
        # No relay configured → the channel is in pure stream mode and
        # any external "webhook" path is operator-relay anyway.
        WeChatProvider().verify_signature(
            channel_config={},
            headers={},
            body=b"{}",
        )


class TestParseInbound:
    def test_simple_relay_payload(self):
        """Relay webhook → InboundMessage. Even with no
        ``context_token`` in the upstream relay payload (some operators
        strip it), we still emit a thread key in the canonical
        ``wechat:<user>:<context>:<session>`` shape so ``post_reply``
        has one parser to maintain."""
        msg = WeChatProvider().parse_inbound(
            {
                "from_user_id": "u1",
                "text": "hi",
                "context_token": "ctx-123",
                "session_id": "sess-1",
            },
            headers={},
        )
        assert msg is not None
        assert msg.thread_key == "wechat:u1:ctx-123:sess-1"
        assert msg.user_text == "hi"
        assert msg.external_user == "u1"
        assert msg.raw["context_token"] == "ctx-123"

    def test_payload_without_context_token_still_parses(self):
        # Some relays drop context_token; we still hand back a key so
        # the dispatcher can run, even though post_reply will refuse
        # to send (no context = iLink rejects).
        msg = WeChatProvider().parse_inbound(
            {"from_user_id": "u1", "text": "hi"},
            headers={},
        )
        assert msg is not None
        assert msg.thread_key.startswith("wechat:u1::")

    def test_missing_text_returns_none(self):
        msg = WeChatProvider().parse_inbound({"from_user_id": "u1"}, headers={})
        assert msg is None

    def test_missing_from_returns_none(self):
        msg = WeChatProvider().parse_inbound({"text": "hello"}, headers={})
        assert msg is None
