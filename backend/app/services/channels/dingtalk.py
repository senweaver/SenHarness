"""DingTalk (钉钉) custom-bot inbound/outbound provider.

DingTalk's "custom robot" webhook posts messages as JSON to a URL
that includes a ``timestamp`` + ``sign`` query-string. We verify the
HMAC-SHA256 signature on every push (when ``sign_secret`` is set in
config) and reject anything older than a 60-second window to block
replay. Outbound replies go to the same webhook URL (``webhook_url``
in config) — DingTalk doesn't maintain server-side thread state for
custom robots, so ``thread_key`` is effectively "this channel".

Reference:
  * https://open.dingtalk.com/document/robots/customize-robot-security-settings
  * https://open.dingtalk.com/document/robots/receive-message

Config shape (config_json):

    webhook_url      (required)  — DingTalk custom-robot webhook URL,
                                   usually includes ``access_token``
                                   as a query param.
    sign_secret      (required)  — the "SEC..." signing secret shown
                                   next to the webhook in the bot
                                   admin UI.
    verify_signatures (optional, default True) — set False during
                                   dev tunnel setup when the proxy
                                   rewrites query strings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundMessage,
    SignatureInvalid,
)

log = logging.getLogger(__name__)

DINGTALK_TIMESTAMP_TOLERANCE_MS = 60 * 1000  # 60 seconds


class DingTalkProvider(ChannelProvider):
    kind = "dingtalk"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="DingTalk (钉钉)",
            description=(
                "DingTalk custom-robot webhook. Verifies HMAC-SHA256 "
                "signatures on inbound pushes (with a 60-second replay "
                "window) and replies by POSTing a markdown message to "
                "the robot's webhook URL."
            ),
            docs_url="https://open.dingtalk.com/document/robots/",
            required_config_fields=("webhook_url", "sign_secret"),
            optional_config_fields=("verify_signatures",),
            supports_outbound=True,
        )

    # ── Signature verification ────────────────────────────
    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        sign_secret = channel_config.get("sign_secret")
        if not sign_secret:
            # Not configured → let the request through (back-compat
            # with channels created before fu-im-real hardening).
            return

        # DingTalk's custom-robot variant signs the query string of
        # the webhook URL, but our ingress route re-exposes the
        # provider-supplied ``timestamp`` / ``sign`` via headers we
        # accept either location.
        timestamp = headers.get("timestamp") or headers.get("x-dingtalk-timestamp")
        sign = headers.get("sign") or headers.get("x-dingtalk-sign")

        if not timestamp or not sign:
            raise SignatureInvalid(
                "dingtalk.missing_signature_headers",
                "DingTalk push missing 'timestamp' / 'sign' headers",
            )

        try:
            ts_ms = int(timestamp)
        except ValueError as e:
            raise SignatureInvalid(
                "dingtalk.bad_timestamp", f"timestamp not an int: {e}"
            ) from e

        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts_ms) > DINGTALK_TIMESTAMP_TOLERANCE_MS:
            raise SignatureInvalid(
                "dingtalk.stale_timestamp",
                f"timestamp outside the replay window ({ts_ms} vs {now_ms})",
            )

        expected = _compute_sign(timestamp, sign_secret)
        if not hmac.compare_digest(expected, sign):
            raise SignatureInvalid(
                "dingtalk.signature_mismatch",
                "DingTalk HMAC signature did not match",
            )

    # ── Inbound parsing ──────────────────────────────────
    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        # DingTalk custom-robot push format:
        #     {
        #       "msgtype": "text",
        #       "text": {"content": "..."},
        #       "senderNick": "...",
        #       "senderStaffId": "...",
        #       "conversationId": "...",
        #       "chatbotUserId": "...",
        #     }
        # We only handle ``text`` — rich types (markdown / image) fall
        # through to "ignore" so the agent doesn't try to respond with
        # garbage.
        msgtype = payload.get("msgtype")
        if msgtype != "text":
            return None

        text_block = payload.get("text") or {}
        user_text = str(text_block.get("content") or "").strip()
        if not user_text:
            return None

        sender = (
            payload.get("senderNick")
            or payload.get("senderStaffId")
            or "unknown"
        )
        thread_key = str(
            payload.get("conversationId")
            or payload.get("senderStaffId")
            or "dingtalk:fallback"
        )

        return InboundMessage(
            thread_key=thread_key,
            user_text=user_text,
            external_user=str(sender),
            raw=payload,
        )

    # ── Outbound reply ───────────────────────────────────
    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        webhook_url = channel_config.get("webhook_url")
        if not webhook_url:
            log.warning("dingtalk channel missing webhook_url; dropping reply")
            return

        # For custom-robot endpoints, outbound messages also need to
        # be signed (per newer DingTalk security guidance). If the
        # operator stored ``sign_secret`` we append the signature
        # query string; otherwise we trust the URL's ``access_token``
        # and ACL rules alone.
        url = webhook_url
        sign_secret = channel_config.get("sign_secret")
        if sign_secret:
            timestamp = str(int(time.time() * 1000))
            sign = _compute_sign(timestamp, sign_secret)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}timestamp={timestamp}&sign={quote_plus(sign)}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "SenHarness", "text": text},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                resp = await cli.post(url, json=payload)
            if resp.status_code >= 300:
                log.warning(
                    "dingtalk post_reply HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError as e:  # pragma: no cover - network path
            log.exception("dingtalk post_reply failed: %s", e)


# ─── Helpers ─────────────────────────────────────────────
def _compute_sign(timestamp: str, secret: str) -> str:
    """Compute DingTalk's ``{timestamp}\\n{secret}`` HMAC-SHA256 signature.

    The signature is base64-encoded then URL-encoded by the caller
    (DingTalk's webhook URL format expects it that way).
    """
    string_to_sign = f"{timestamp}\n{secret}".encode()
    key = secret.encode("utf-8")
    digest = hmac.new(key, string_to_sign, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")
