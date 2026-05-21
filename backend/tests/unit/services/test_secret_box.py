"""Envelope-encryption helpers for channel config_json."""

from __future__ import annotations

from app.services.channels._secret_box import (
    SECRET_FIELDS,
    decrypt_config,
    decrypt_field,
    encrypt_config,
    encrypt_field,
)


class TestField:
    def test_roundtrip(self):
        sealed = encrypt_field("super-secret-bot-token")
        assert sealed.startswith("enc:v1:")
        assert decrypt_field(sealed) == "super-secret-bot-token"

    def test_idempotent_on_already_sealed(self):
        sealed_once = encrypt_field("hello")
        sealed_twice = encrypt_field(sealed_once)
        assert sealed_once == sealed_twice
        assert decrypt_field(sealed_twice) == "hello"

    def test_empty_passthrough(self):
        assert encrypt_field("") == ""
        assert decrypt_field("") == ""

    def test_plaintext_passthrough_on_decrypt(self):
        # Legacy rows (or non-secret fields stored verbatim) must
        # round-trip without ``enc:v1:`` prefix.
        assert decrypt_field("plaintext") == "plaintext"


class TestConfig:
    def test_only_secret_fields_sealed(self):
        config = {
            "name": "my-bot",  # not a secret field
            "bot_token": "tok-123",
            "verify_signatures": True,
        }
        sealed = encrypt_config(config)
        assert sealed["name"] == "my-bot"
        assert sealed["verify_signatures"] is True
        assert sealed["bot_token"].startswith("enc:v1:")

        plain = decrypt_config(sealed)
        assert plain["bot_token"] == "tok-123"
        assert plain["name"] == "my-bot"

    def test_secret_fields_set_is_consistent(self):
        # Doubly assert the public set used by mask_config + crypto helpers
        # stays the same — drift here is the cause of the silent "leaked
        # secret" bug.
        assert "bot_token" in SECRET_FIELDS
        assert "encoding_aes_key" in SECRET_FIELDS
        assert "secret" in SECRET_FIELDS

    def test_empty_secret_left_alone(self):
        # Operator deliberately blanking a token (e.g. WeChat logout)
        # must not result in ``enc:v1:`` of empty string.
        out = encrypt_config({"bot_token": ""})
        assert out["bot_token"] == ""
