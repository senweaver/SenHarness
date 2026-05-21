"""Verification for fu-im-real + fu-oauth-mfa.

IM signature checks (fu-im-real):
  1. Slack HMAC: signed request passes; mutated body fails; stale timestamp
     fails; missing headers fail; channel with ``verify_signatures=false``
     skips the check.
  2. Feishu token: matching ``token`` in body passes; mismatch fails.
  3. Discord ed25519: valid signature passes; tampered body fails.

MFA (fu-oauth-mfa):
  4. ``setup()`` writes a pending secret; ``verify_login_code`` rejects while
     pending.
  5. ``activate()`` with a fresh TOTP code flips to live; subsequent login
     codes verify.
  6. ``disable()`` clears the secret.

Run:  ``python -m scripts.fu_im_mfa_verify``
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time

logging.basicConfig(level=logging.WARNING)

from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.session import get_session_factory
from app.db.repository import AsyncRepository
from app.services import mfa as mfa_svc
from app.services.channels.base import SignatureInvalid
from app.services.channels.discord import DiscordProvider
from app.services.channels.feishu import FeishuProvider
from app.services.channels.slack import SlackProvider


# ─── fu-im-real ───────────────────────────────────────────
def _slack_sign(secret: str, ts: str, body: bytes) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack() -> None:
    provider = SlackProvider()
    secret = "test_signing_secret_abc"
    cfg = {"signing_secret": secret}
    body = b'{"type":"event_callback","event":{"type":"message","text":"hi"}}'
    ts = str(int(time.time()))
    good_sig = _slack_sign(secret, ts, body)

    # Good case
    provider.verify_signature(
        channel_config=cfg,
        headers={
            "X-Slack-Signature": good_sig,
            "X-Slack-Request-Timestamp": ts,
        },
        body=body,
    )
    # Tampered body
    try:
        provider.verify_signature(
            channel_config=cfg,
            headers={
                "X-Slack-Signature": good_sig,
                "X-Slack-Request-Timestamp": ts,
            },
            body=body + b"X",
        )
        raise AssertionError("should have rejected tampered body")
    except SignatureInvalid as e:
        assert e.code == "slack.bad_signature", e.code

    # Stale timestamp (10 minutes old).
    old_ts = str(int(time.time()) - 10 * 60)
    old_sig = _slack_sign(secret, old_ts, body)
    try:
        provider.verify_signature(
            channel_config=cfg,
            headers={
                "X-Slack-Signature": old_sig,
                "X-Slack-Request-Timestamp": old_ts,
            },
            body=body,
        )
        raise AssertionError("should have rejected stale ts")
    except SignatureInvalid as e:
        assert e.code == "slack.timestamp_skew", e.code

    # Missing headers
    try:
        provider.verify_signature(channel_config=cfg, headers={}, body=body)
        raise AssertionError("should have rejected missing headers")
    except SignatureInvalid as e:
        assert e.code == "slack.missing_headers", e.code

    # Explicit opt-out should pass anything.
    provider.verify_signature(
        channel_config={"signing_secret": secret, "verify_signatures": False},
        headers={},
        body=body,
    )

    print("  [slack] signed ok / tamper / stale / missing / opt-out  (PASS)")


def verify_feishu() -> None:
    provider = FeishuProvider()
    token = "my-verify-token"
    payload = {
        "header": {"token": token, "event_type": "im.message.receive_v1"},
        "event": {},
    }
    body = json.dumps(payload).encode()
    cfg = {"verification_token": token}
    provider.verify_signature(channel_config=cfg, headers={}, body=body)

    # 1.0 style (top-level token) also accepted.
    body2 = json.dumps({"token": token, "challenge": "x"}).encode()
    provider.verify_signature(channel_config=cfg, headers={}, body=body2)

    # Mismatch
    try:
        provider.verify_signature(
            channel_config=cfg,
            headers={},
            body=json.dumps({"token": "other", "header": {"token": "other"}}).encode(),
        )
        raise AssertionError("should reject mismatched token")
    except SignatureInvalid as e:
        assert e.code == "feishu.bad_token", e.code

    print("  [feishu] good / 1.0-style / mismatch  (PASS)")


def verify_discord() -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        print("  [discord] skipped — cryptography not installed")
        return

    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()

    provider = DiscordProvider()
    cfg = {"public_key": pub_hex}
    ts = str(int(time.time()))
    body = b'{"type":1}'
    sig_hex = priv.sign(ts.encode() + body).hex()

    provider.verify_signature(
        channel_config=cfg,
        headers={
            "X-Signature-Ed25519": sig_hex,
            "X-Signature-Timestamp": ts,
        },
        body=body,
    )

    # Tampered body
    try:
        provider.verify_signature(
            channel_config=cfg,
            headers={
                "X-Signature-Ed25519": sig_hex,
                "X-Signature-Timestamp": ts,
            },
            body=body + b"X",
        )
        raise AssertionError("should reject tampered body")
    except SignatureInvalid as e:
        assert e.code == "discord.bad_signature", e.code

    print("  [discord] signed / tamper  (PASS)")


# ─── fu-oauth-mfa ─────────────────────────────────────────
async def verify_mfa() -> None:
    import uuid

    from app.core.security import hash_password

    factory = get_session_factory()
    async with factory() as db:
        ident = Identity(
            email=f"mfa-test-{uuid.uuid4().hex[:6]}@mfa.local",
            name="mfa-test",
            password_hash=hash_password("x" * 12),
            status=IdentityStatus.ACTIVE,
            platform_role=PlatformRole.USER,
            profile_json={},
        )
        db.add(ident)
        await db.commit()
        await db.refresh(ident)

    # setup
    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.identity import IdentityRepository

        ident = await IdentityRepository(db).get(ident.id)
        setup = await mfa_svc.setup(db, identity=ident)
        await db.commit()

    assert setup.otpauth_uri.startswith("otpauth://"), setup.otpauth_uri
    assert len(setup.secret) >= 16
    # pending state — login code should NOT work yet
    factory = get_session_factory()
    async with factory() as db:
        ident = await AsyncRepository(db, Identity).get(ident.id)
    assert (ident.mfa_secret_ref or "").startswith("pending:")
    assert mfa_svc.verify_login_code(ident, "000000") is False

    # activate with a real TOTP code
    import pyotp

    totp = pyotp.TOTP(setup.secret)
    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.identity import IdentityRepository

        ident = await IdentityRepository(db).get(ident.id)
        ok = await mfa_svc.activate(db, identity=ident, code=totp.now())
        assert ok
        await db.commit()
        await db.refresh(ident)
    assert mfa_svc.is_enabled(ident)

    # a fresh code from the same secret should verify
    assert mfa_svc.verify_login_code(ident, totp.now())
    # a wrong code should fail
    assert mfa_svc.verify_login_code(ident, "000000") is False

    # disable
    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.identity import IdentityRepository

        ident = await IdentityRepository(db).get(ident.id)
        await mfa_svc.disable(db, identity=ident)
        await db.commit()
        await db.refresh(ident)
    assert ident.mfa_secret_ref is None

    print("  [mfa] setup → pending → activate → verify → disable  (PASS)")


async def main() -> None:
    verify_slack()
    verify_feishu()
    verify_discord()
    await verify_mfa()
    print("\n[PASS] fu-im-real + fu-oauth-mfa verification complete")


if __name__ == "__main__":
    asyncio.run(main())
