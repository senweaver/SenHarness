"""M0.8 — generic webhook HMAC signature verification."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.generic import WebhookProvider


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_passes_when_disabled() -> None:
    provider = WebhookProvider()
    provider.verify_signature(
        channel_config={"verify_signatures": False},
        headers={},
        body=b"{}",
    )


def test_verify_signature_default_requires_secret() -> None:
    provider = WebhookProvider()
    with pytest.raises(SignatureInvalid) as exc:
        provider.verify_signature(channel_config={}, headers={}, body=b"{}")
    assert exc.value.code == "webhook.hmac_secret_unset"


def test_verify_signature_rejects_missing_header() -> None:
    provider = WebhookProvider()
    with pytest.raises(SignatureInvalid) as exc:
        provider.verify_signature(
            channel_config={"hmac_secret": "shh"},
            headers={},
            body=b"hi",
        )
    assert exc.value.code == "webhook.missing_signature_header"


def test_verify_signature_rejects_bad_hmac() -> None:
    provider = WebhookProvider()
    with pytest.raises(SignatureInvalid) as exc:
        provider.verify_signature(
            channel_config={"hmac_secret": "shh"},
            headers={"x-hmac-signature": "deadbeef"},
            body=b"hi",
        )
    assert exc.value.code == "webhook.bad_signature"


def test_verify_signature_accepts_correct_hmac() -> None:
    provider = WebhookProvider()
    body = b'{"text":"hello"}'
    provider.verify_signature(
        channel_config={"hmac_secret": "shh"},
        headers={"x-hmac-signature": _sign("shh", body)},
        body=body,
    )


def test_verify_signature_accepts_sha256_prefix() -> None:
    provider = WebhookProvider()
    body = b"alpha"
    sig = _sign("shh", body)
    provider.verify_signature(
        channel_config={"hmac_secret": "shh"},
        headers={"X-HMAC-Signature": f"sha256={sig}"},
        body=body,
    )
