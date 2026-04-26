"""Tests for the Telegram provider."""

from __future__ import annotations

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.telegram import TelegramProvider, _parse_thread_key


class TestVerifySignature:
    def test_valid_secret_token_passes(self):
        provider = TelegramProvider()
        provider.verify_signature(
            channel_config={"secret_token": "abc123"},
            headers={"x-telegram-bot-api-secret-token": "abc123"},
            body=b"{}",
        )

    def test_missing_secret_header_rejected(self):
        provider = TelegramProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"secret_token": "abc123"},
                headers={},
                body=b"{}",
            )
        assert exc.value.code == "telegram.missing_secret_token"

    def test_mismatch_secret_header_rejected(self):
        provider = TelegramProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"secret_token": "abc123"},
                headers={"x-telegram-bot-api-secret-token": "zzz"},
                body=b"{}",
            )
        assert exc.value.code == "telegram.secret_token_mismatch"

    def test_verify_signatures_false_skips_check(self):
        provider = TelegramProvider()
        provider.verify_signature(
            channel_config={"secret_token": "abc123", "verify_signatures": False},
            headers={},
            body=b"{}",
        )


class TestParseInbound:
    def test_message_parsed(self):
        provider = TelegramProvider()
        msg = provider.parse_inbound(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "text": " hello ",
                    "chat": {"id": 1234},
                    "from": {"id": 99, "username": "alice"},
                },
            },
            headers={},
        )
        assert msg is not None
        assert msg.user_text == "hello"
        assert msg.thread_key == "telegram:1234"
        assert msg.external_user == "alice"

    def test_message_thread_id_parsed(self):
        provider = TelegramProvider()
        msg = provider.parse_inbound(
            {
                "message": {
                    "message_id": 10,
                    "text": "hello",
                    "chat": {"id": 1234},
                    "message_thread_id": 7,
                    "from": {"id": 99},
                }
            },
            headers={},
        )
        assert msg is not None
        assert msg.thread_key == "telegram:1234:7"

    def test_empty_text_ignored(self):
        provider = TelegramProvider()
        msg = provider.parse_inbound(
            {"message": {"text": "   ", "chat": {"id": 1}}},
            headers={},
        )
        assert msg is None


class TestParseThreadKey:
    def test_parse_chat_only(self):
        assert _parse_thread_key("telegram:100") == (100, None)

    def test_parse_chat_and_topic(self):
        assert _parse_thread_key("telegram:100:2") == (100, 2)

    def test_parse_malformed(self):
        assert _parse_thread_key("bad") == (None, None)
