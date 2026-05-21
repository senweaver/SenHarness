"""Example channel plugin entry point (M3.5).

The host calls :func:`register` exactly once after the manifest has
been validated, the ed25519 signature checked, and a platform admin
has approved the matching ``plugin_registry`` row. The factory we
hand to ``ctx.register_channel_kind`` produces a fresh
:class:`ChannelProvider` instance per registration.

This plugin contributes a fictional ``test_channel`` kind that
mirrors the bundled webhook adapter: it accepts any JSON payload,
extracts ``user_text`` + ``thread_key``, and never replies. Drop it
under ``STORAGE_LOCAL_PATH/plugins/example_channel_plugin/`` to see
the channel kind appear in ``GET /api/v1/channels/kinds``.
"""

from __future__ import annotations

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundMessage,
)


class TestChannelProvider(ChannelProvider):
    kind = "test_channel"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Test Channel (plugin example)",
            description=(
                "Reference channel adapter installed by example_channel_plugin. "
                "Accepts arbitrary JSON; never replies. Documentation only."
            ),
            required_config_fields=("inbound_label",),
            optional_config_fields=("notes",),
            supports_outbound=False,
            supported_modes=("webhook",),
            default_mode="webhook",
        )

    async def parse_inbound(
        self, payload: dict, headers: dict
    ) -> InboundMessage | None:
        text = str(payload.get("user_text") or payload.get("text") or "").strip()
        thread_key = str(
            payload.get("thread_key") or payload.get("thread_id") or "default"
        )
        if not text:
            return None
        return InboundMessage(
            thread_key=thread_key,
            user_text=text,
            external_user=str(payload.get("from") or "anonymous"),
            raw=dict(payload),
        )


def register(ctx) -> None:
    ctx.register_channel_kind("test_channel", TestChannelProvider)
