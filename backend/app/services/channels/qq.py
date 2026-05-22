"""QQ Bot — Tencent QQ open-platform robot.

Two transport modes:

* **stream** (default) — the SenHarness process dials Tencent's
  WebSocket gateway with the ``qq-botpy`` SDK. No public IP required.
  Inbound events ``GROUP_AT_MESSAGE_CREATE`` / ``C2C_MESSAGE_CREATE``
  flow into :func:`run_stream`; outbound replies use ``client.api``.

* **webhook** — Tencent's V2 callback. We verify the request with
  Ed25519 signatures derived from the bot's ``app_secret`` (seed =
  the secret repeated to 32 bytes) and respond to the op=13 plain
  token handshake by signing it back. The same signing helpers are
  shared between handshake + verify_signature paths.

Config shape (config_json):

    app_id      (required) — bot's AppID from QQ open platform
    app_secret  (required) — bot's AppSecret (used for signing AND token fetch)
    sandbox     (optional, default False) — set True for the sandbox env
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING, Any

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundDispatch,
    InboundMessage,
    SignatureInvalid,
)

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


def _ed25519_seed_from_secret(secret: str) -> bytes:
    """Derive the 32-byte Ed25519 seed from the QQ bot AppSecret.

    Tencent's V2 callback rule: repeat the bytes of ``app_secret`` until
    you reach (or exceed) 32 bytes, then truncate. Truncated bytes are
    used directly as the Ed25519 *seed* (private key material).

    We expose this as a module-level helper so the sign + verify paths
    stay byte-identical and so the test suite can pin the math.
    """
    if not secret:
        raise ValueError("qq.empty_secret")
    raw = secret.encode("utf-8")
    while len(raw) < 32:
        raw += raw
    return raw[:32]


def _sign_plain_token(plain_token: str, event_ts: str, secret: str) -> str:
    """Compute the V2 op=13 handshake signature.

    Tencent specifies: signature = Ed25519_sign(event_ts || plain_token).
    We return the lowercase hex digest the QQ open platform expects on
    the WSS handshake response (or, in webhook mode, in the JSON body).
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("qq provider requires the 'cryptography' package") from e

    seed = _ed25519_seed_from_secret(secret)
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    msg = (event_ts + plain_token).encode("utf-8")
    sig = sk.sign(msg)
    return sig.hex()


def _verify_v2_signature(*, secret: str, headers: dict[str, str], body: bytes) -> None:
    """Validate the V2 webhook signature header pair.

    The official spec passes:

        X-Signature-Ed25519: <hex>
        X-Signature-Timestamp: <unix-seconds>

    We re-derive the Ed25519 verifying key from the AppSecret seed and
    check ``sign(timestamp || body)``. Any mismatch raises
    :class:`SignatureInvalid` so the ingress route translates it to a 403.
    """
    h = {k.lower(): v for k, v in headers.items()}
    sig_hex = h.get("x-signature-ed25519")
    ts = h.get("x-signature-timestamp")
    if not sig_hex or not ts:
        raise SignatureInvalid(
            "qq.missing_signature_headers",
            "QQ V2 push missing X-Signature-Ed25519 / X-Signature-Timestamp",
        )

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as e:  # pragma: no cover
        raise SignatureInvalid("qq.crypto_missing", "cryptography library not available") from e

    try:
        seed = _ed25519_seed_from_secret(secret)
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        pk = sk.public_key()
        msg = ts.encode("utf-8") + body
        pk.verify(bytes.fromhex(sig_hex), msg)
    except (ValueError, InvalidSignature) as e:
        raise SignatureInvalid("qq.signature_mismatch", "QQ V2 signature mismatch") from e


class QQBotProvider(ChannelProvider):
    kind = "qq"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="QQ Bot",
            description=(
                "QQ open-platform robot. Defaults to stream mode "
                "(WebSocket via qq-botpy) so channels work without "
                "a public IP; webhook mode is also supported with "
                "V2 Ed25519 signature verification."
            ),
            docs_url="https://bot.q.qq.com/wiki/",
            required_config_fields=("app_id", "app_secret"),
            optional_config_fields=("sandbox", "verify_signatures"),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
            stream_requires_extra="channels-stream",
            # QQ's V2 protocol uses the same AppID/AppSecret pair on both
            # transports — the secret seeds the Ed25519 webhook signature
            # AND the qq-botpy stream handshake. We still emit per-mode
            # tuples so the form doesn't have to special-case "no
            # override means use the global list".
            mode_required_fields={
                "stream": ("app_id", "app_secret"),
                "webhook": ("app_id", "app_secret"),
            },
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        try:
            import botpy  # noqa: F401
        except ImportError:
            return False
        return True

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        secret = channel_config.get("app_secret")
        if not secret:
            return
        _verify_v2_signature(secret=secret, headers=headers, body=body)

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        # V2 op=13 plain-token handshake: the platform sends
        #   {"op": 13, "d": {"plain_token": "...", "event_ts": "..."}}
        # and expects {"plain_token": "...", "signature": "<hex>"}.
        # The ingress is responsible for surfacing the channel's
        # ``app_secret`` via ``payload["_app_secret"]`` (we don't
        # have the channel row at this layer).
        if payload.get("op") != 13:
            return None
        d = payload.get("d") or {}
        plain_token = d.get("plain_token") or ""
        event_ts = d.get("event_ts") or ""
        secret = payload.get("_app_secret") or ""
        if not (plain_token and event_ts and secret):
            return None
        try:
            sig = _sign_plain_token(plain_token, event_ts, secret)
        except Exception as e:  # pragma: no cover
            log.warning("qq op=13 sign failed: %s", e)
            return None
        return {"plain_token": plain_token, "signature": sig}

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        # V2 callback envelope: {"op": 0, "t": "<event>", "d": {...}}
        # where ``t`` is GROUP_AT_MESSAGE_CREATE / C2C_MESSAGE_CREATE /
        # AT_MESSAGE_CREATE (guild). We only respond when there's user
        # text — control events (RESUMED, READY, etc.) get ignored.
        if payload.get("op") == 13:
            return None
        event_type = payload.get("t") or ""
        if event_type not in {
            "AT_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "C2C_MESSAGE_CREATE",
        }:
            return None
        d = payload.get("d") or {}
        content = str(d.get("content") or "").strip()
        if not content:
            return None

        author = d.get("author") or {}
        user_id = (
            author.get("user_openid")
            or author.get("member_openid")
            or author.get("id")
            or "qq_user"
        )

        # Different events expose different stable thread identifiers.
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            thread = f"qq_group:{d.get('group_openid') or d.get('group_id') or ''}"
        elif event_type == "C2C_MESSAGE_CREATE":
            thread = f"qq_c2c:{user_id}"
        else:
            thread = f"qq_guild:{d.get('channel_id') or d.get('guild_id') or ''}"

        return InboundMessage(
            thread_key=thread,
            user_text=content,
            external_user=str(user_id),
            raw={
                "event_type": event_type,
                "id": d.get("id"),
                "guild_id": d.get("guild_id"),
                "channel_id": d.get("channel_id"),
                "group_openid": d.get("group_openid"),
            },
        )

    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        # Without a held-open botpy client, webhook-mode replies need the
        # REST endpoint. We log + skip rather than crash since QQ's V2
        # REST surface depends on app permissions; operators using webhook
        # mode typically have their own relay anyway.
        log.info(
            "qq webhook-mode reply not yet wired; thread_key=%r len=%d",
            thread_key,
            len(text),
        )

    async def send_text(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        from app.services.channels._qq_stream import send_text as _send

        await _send(channel_config=channel_config, thread_key=thread_key, text=text)

    async def run_stream(
        self,
        *,
        channel: Channel,
        dispatch: InboundDispatch,
        stop: asyncio.Event,
    ) -> None:
        from app.services.channels._qq_stream import run_botpy_stream

        await run_botpy_stream(channel=channel, dispatch=dispatch, stop=stop)


# ── Lightweight signing API for tests ────────────────────────────
# Re-export the helpers so test files can import without crossing the
# public/private barrier. (They live here because they're the "real"
# callable surface, not in the stream sub-module.)
__all__ = [
    "QQBotProvider",
    "_ed25519_seed_from_secret",
    "_sign_plain_token",
    "_verify_v2_signature",
]


# Silence "unused import" lints for stdlib helpers we keep around so
# alternate transports (HMAC pings, JSON envelope packing) compile
# without re-importing.
_ = (hashlib, hmac, json)
