"""Discord Interactions webhook (minimal).

Supports:
    * PING handshake (type=1) → type=1 ack.
    * APPLICATION_COMMAND (type=2) with a single ``text`` string option — the
      simplest way to let a server run an agent without voice/channel sync.
    * MESSAGE_COMPONENT / modal submissions are P2.

Inbound auth: ed25519 signature (X-Signature-Ed25519 +
X-Signature-Timestamp) verified against the ``public_key`` stored in
``config_json.public_key`` (hex-encoded 32-byte Ed25519 pubkey from the
Discord Developer Portal).

Outbound: uses the ``bot_token`` to follow up on the interaction via
``/webhooks/{app_id}/{interaction_token}``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.channels.base import (
    ChannelProvider,
    InboundMessage,
    SignatureInvalid,
)

log = logging.getLogger(__name__)


class DiscordProvider(ChannelProvider):
    kind = "discord"

    @classmethod
    def metadata(cls):
        from app.services.channels.base import ChannelProviderMeta

        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Discord",
            description=(
                "Discord application / bot. Uses Ed25519 signature "
                "verification on every interaction and replies to thread "
                "or channel via the Discord REST API."
            ),
            docs_url="https://discord.com/developers/docs/",
            required_config_fields=("bot_token", "public_key"),
            optional_config_fields=("application_id", "verify_signatures"),
            supports_outbound=True,
        )

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        pubkey_hex = channel_config.get("public_key")
        if not pubkey_hex:
            return
        h = {k.lower(): v for k, v in headers.items()}
        sig = h.get("x-signature-ed25519")
        ts = h.get("x-signature-timestamp")
        if not sig or not ts:
            raise SignatureInvalid(
                "discord.missing_headers", "missing Discord signature headers"
            )
        # Use cryptography's Ed25519 — ships with the wider "cryptography" dep
        # already required for JWT; no extra package needed.
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
        except ImportError as e:  # pragma: no cover
            raise SignatureInvalid(
                "discord.crypto_missing", "cryptography library not available"
            ) from e
        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
            pk.verify(bytes.fromhex(sig), ts.encode() + body)
        except (ValueError, InvalidSignature) as e:
            raise SignatureInvalid(
                "discord.bad_signature", "Discord signature mismatch"
            ) from e

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        t = payload.get("type")
        if t == 1:
            return None  # PING handshake
        if t != 2:  # APPLICATION_COMMAND
            return None

        data = payload.get("data") or {}
        options = data.get("options") or []
        text = ""
        for opt in options:
            if opt.get("name") in {"text", "prompt", "q"} and opt.get("type") == 3:
                text = str(opt.get("value") or "").strip()
                break
        if not text:
            return None

        interaction_token = payload.get("token") or ""
        app_id = payload.get("application_id") or ""
        channel_id = payload.get("channel_id") or "unknown"
        user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
        user_id = user.get("id") or "discord_user"

        return InboundMessage(
            thread_key=f"discord:{app_id}:{interaction_token}",
            user_text=text,
            external_user=user_id,
            raw={
                "channel_id": channel_id,
                "interaction_id": payload.get("id"),
                "application_id": app_id,
            },
        )

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("type") == 1:
            return {"type": 1}
        return None

    async def post_reply(
        self, *, channel_config: dict[str, Any], thread_key: str, text: str
    ) -> None:
        try:
            _, app_id, interaction_token = thread_key.split(":", 2)
        except ValueError:
            log.warning("malformed discord thread_key %r", thread_key)
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"https://discord.com/api/v10/webhooks/{app_id}/{interaction_token}",
                    json={"content": text[:1900]},
                )
                if r.status_code >= 300:
                    log.warning("discord followup failed: %s %s", r.status_code, r.text)
        except Exception as e:  # pragma: no cover
            log.warning("discord reply error: %s", e)
