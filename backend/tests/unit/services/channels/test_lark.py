"""Tests for the Lark provider (international Feishu sibling)."""

from __future__ import annotations

import json

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.lark import LarkProvider


class TestMetadata:
    def test_required_fields_locked(self):
        meta = LarkProvider.metadata()
        assert meta.kind == "lark"
        assert "Lark" in meta.display_name
        assert set(meta.required_config_fields) >= {
            "app_id",
            "app_secret",
            "verification_token",
        }
        assert meta.supports_outbound is True

    def test_supports_stream_default_mode(self):
        meta = LarkProvider.metadata()
        assert "stream" in meta.supported_modes
        assert "webhook" in meta.supported_modes
        assert meta.default_mode == "stream"
        assert meta.stream_requires_extra == "channels-stream"


class TestVerifySignature:
    def test_token_match_passes(self):
        body = json.dumps({"token": "abc", "type": "x"}).encode()
        LarkProvider().verify_signature(
            channel_config={"verification_token": "abc"},
            headers={},
            body=body,
        )

    def test_token_mismatch_rejected(self):
        body = json.dumps({"token": "wrong"}).encode()
        with pytest.raises(SignatureInvalid) as exc:
            LarkProvider().verify_signature(
                channel_config={"verification_token": "abc"},
                headers={},
                body=body,
            )
        assert exc.value.code == "lark.bad_token"

    def test_2_0_header_token_supported(self):
        body = json.dumps({"header": {"token": "v2-token"}}).encode()
        LarkProvider().verify_signature(
            channel_config={"verification_token": "v2-token"},
            headers={},
            body=body,
        )

    def test_no_secret_skips_check(self):
        # Configuration omitted → back-compat allow-through.
        LarkProvider().verify_signature(
            channel_config={},
            headers={},
            body=b"{}",
        )

    def test_opt_out_flag(self):
        LarkProvider().verify_signature(
            channel_config={
                "verification_token": "x",
                "verify_signatures": False,
            },
            headers={},
            body=b"{}",
        )


class TestParseInbound:
    def test_url_verification_returns_none(self):
        msg = LarkProvider().parse_inbound(
            {"type": "url_verification", "challenge": "xyz"},
            headers={},
        )
        assert msg is None

    def test_handshake_response_echoes_challenge(self):
        resp = LarkProvider().handshake_response({"type": "url_verification", "challenge": "xyz"})
        assert resp == {"challenge": "xyz"}

    def test_message_event_extracts_text(self):
        payload = {
            "header": {
                "event_type": "im.message.receive_v1",
                "event_id": "evt-1",
            },
            "event": {
                "message": {
                    "chat_id": "chat-1",
                    "message_id": "msg-1",
                    "content": json.dumps({"text": "hi agent"}),
                },
                "sender": {"sender_id": {"open_id": "u1"}},
            },
        }
        msg = LarkProvider().parse_inbound(payload, headers={})
        assert msg is not None
        assert msg.user_text == "hi agent"
        assert msg.thread_key.startswith("lark:chat-1")
        assert msg.external_user == "u1"
