"""Generic webhook provider — accepts ``{"text": "...", "thread": "..."}``.

No outbound reply; the answer only shows up in the SenHarness chat UI.
Useful for Zapier / cURL / custom integrations.

Inbound auth (M0.8):
    ``verify_signatures`` defaults to ``True``. When set, every request
    must carry an ``X-HMAC-Signature`` header that matches
    ``hex(hmac_sha256(hmac_secret, raw_body))``. Without ``hmac_secret``
    on the row, the signature check fails closed and the ingress
    returns 401 — operators see the failure in audit_events
    (``channel.signature_required_but_unset``) and can either configure
    a secret or explicitly opt out via ``verify_signatures=false``.
"""

from __future__ import annotations

import hmac
import uuid
from hashlib import sha256
from typing import Any

from app.services.channels.base import (
    ChannelProvider,
    InboundMessage,
    SignatureInvalid,
)


class WebhookProvider(ChannelProvider):
    kind = "webhook"

    @classmethod
    def metadata(cls):
        from app.services.channels.base import ChannelProviderMeta

        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Generic webhook",
            description=(
                "Minimal JSON inbound — POST arbitrary payloads with a "
                "``text`` and optional ``thread_id`` / ``user`` fields. "
                "No outbound reply; suitable for receive-only integrations "
                "like monitoring alerts or form-submit triggers. Requires "
                "an ``X-HMAC-Signature: hex(hmac_sha256(secret, body))`` "
                "header by default."
            ),
            docs_url="/docs/extensions-and-governance.md#channel-providers",
            required_config_fields=(),
            optional_config_fields=("verify_signatures", "hmac_secret"),
            supports_outbound=False,
        )

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        # Explicit opt-out wins; default ``verify_signatures`` is True.
        verify_flag = channel_config.get("verify_signatures")
        if verify_flag is False:
            return

        secret = channel_config.get("hmac_secret")
        if not secret:
            # Default-deny: signing is required by default. The ingress
            # converts this into HTTP 401 + writes
            # ``channel.signature_required_but_unset`` so admins notice.
            raise SignatureInvalid(
                "webhook.hmac_secret_unset",
                "hmac_secret required when verify_signatures is true",
            )
        h = {k.lower(): v for k, v in headers.items()}
        sig = (h.get("x-hmac-signature") or "").strip().lower()
        if not sig:
            raise SignatureInvalid(
                "webhook.missing_signature_header",
                "missing X-HMAC-Signature header",
            )
        # Tolerate the conventional ``sha256=`` prefix some clients add.
        if sig.startswith("sha256="):
            sig = sig[len("sha256=") :]
        secret_bytes = secret.encode() if isinstance(secret, str) else bytes(secret)
        expected = hmac.new(secret_bytes, body, sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise SignatureInvalid("webhook.bad_signature", "hmac mismatch")

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return None
        thread = str(
            payload.get("thread")
            or payload.get("thread_id")
            or payload.get("session_id")
            or f"webhook:{uuid.uuid4().hex[:8]}"
        )
        user = str(payload.get("user") or payload.get("from") or "webhook")
        return InboundMessage(
            thread_key=thread,
            user_text=text,
            external_user=user,
            raw=payload,
        )
