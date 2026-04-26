"""Generic webhook provider — accepts ``{"text": "...", "thread": "..."}``.

No outbound reply; the answer only shows up in the SenHarness chat UI.
Useful for Zapier / cURL / custom integrations.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.services.channels.base import ChannelProvider, InboundMessage


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
                "like monitoring alerts or form-submit triggers."
            ),
            docs_url="/docs/channels.md",
            required_config_fields=(),
            optional_config_fields=("verify_signatures",),
            supports_outbound=False,
        )

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
