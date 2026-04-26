"""Microsoft Teams outgoing-webhook provider.

Inbound security:
Teams outgoing webhooks sign each request body with HMAC-SHA256 and
send it in ``Authorization: HMAC <base64-digest>``. We validate this
signature with ``config_json.signing_secret`` when enabled.

Inbound payload:
We normalize the message text + sender + conversation id so repeated
messages from the same Teams thread route into one SenHarness session.

Outbound:
Teams outgoing-webhook callbacks expect immediate HTTP responses, while
SenHarness replies asynchronously. To support outbound posting we use an
optional ``incoming_webhook_url`` and send plain text cards there.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundMessage,
    SignatureInvalid,
)

log = logging.getLogger(__name__)


class TeamsProvider(ChannelProvider):
    kind = "teams"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Microsoft Teams",
            description=(
                "Teams outgoing webhook ingress with HMAC signature "
                "verification. Optional outgoing posting via an "
                "incoming webhook URL."
            ),
            docs_url="https://learn.microsoft.com/microsoftteams/platform/",
            required_config_fields=("signing_secret",),
            optional_config_fields=("incoming_webhook_url", "verify_signatures"),
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
        secret = str(channel_config.get("signing_secret") or "").strip()
        if not secret:
            return

        auth = (
            headers.get("authorization")
            or headers.get("Authorization")
            or headers.get("AUTHORIZATION")
            or ""
        )
        if not auth.lower().startswith("hmac "):
            raise SignatureInvalid(
                "teams.missing_signature",
                "missing Teams HMAC Authorization header",
            )
        supplied = auth.split(" ", 1)[1].strip()
        if not supplied:
            raise SignatureInvalid(
                "teams.missing_signature",
                "empty Teams HMAC Authorization value",
            )

        expected = _compute_hmac_digest(secret, body)
        if not hmac.compare_digest(expected, supplied):
            raise SignatureInvalid(
                "teams.signature_mismatch",
                "Teams request signature mismatch",
            )

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return None

        from_block = payload.get("from") or {}
        conversation = payload.get("conversation") or {}
        user = from_block.get("name") or from_block.get("id") or "teams_user"
        conv_id = conversation.get("id") or payload.get("id") or "teams:fallback"

        return InboundMessage(
            thread_key=str(conv_id),
            user_text=text,
            external_user=str(user),
            raw={
                "id": payload.get("id"),
                "service_url": payload.get("serviceUrl"),
                "channel_id": payload.get("channelId"),
                "conversation_id": conv_id,
            },
        )

    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        webhook_url = str(channel_config.get("incoming_webhook_url") or "").strip()
        if not webhook_url:
            log.warning("teams channel missing incoming_webhook_url; skipping reply")
            return

        payload = {"type": "message", "text": text[:3900]}
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                resp = await cli.post(webhook_url, json=payload)
            if resp.status_code >= 300:
                log.warning(
                    "teams post_reply HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError as e:  # pragma: no cover - network path
            log.exception("teams post_reply failed: %s", e)


def _compute_hmac_digest(secret: str, body: bytes) -> str:
    """Compute Teams outgoing-webhook HMAC digest.

    Teams docs describe an HMAC-SHA256 over the exact request body.
    The shared secret may be plain text or base64-encoded depending on
    how operators copied it, so we support both forms.
    """
    secret_bytes = _decode_secret(secret)
    digest = hmac.new(secret_bytes, body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _decode_secret(secret: str) -> bytes:
    try:
        decoded = base64.b64decode(secret, validate=True)
    except Exception:
        return secret.encode("utf-8")
    return decoded or secret.encode("utf-8")
