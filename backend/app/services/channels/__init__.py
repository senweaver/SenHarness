"""IM channel provider registry.

Providers register themselves at import time. The ingress layer
routes incoming webhooks to ``get_provider(kind)``; the frontend
channel-create form reads ``describe_providers()`` to know which
kinds are installable and what config they need.

Adding a community provider:

    # my_provider.py
    from app.services.channels.base import ChannelProvider
    from app.services.channels import register_provider

    class MyProvider(ChannelProvider):
        kind = "my_provider"
        ...

    register_provider(MyProvider())

Then import ``my_provider`` once from anywhere that runs at app
startup (typically a package __init__) so the registration fires.
"""

from __future__ import annotations

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundMessage,
    SignatureInvalid,
)

_REGISTRY: dict[str, ChannelProvider] = {}


def register_provider(provider: ChannelProvider) -> ChannelProvider:
    """Register ``provider`` under ``provider.kind``.

    Called once per provider at import time. Re-registering the same
    kind replaces the previous entry (useful for tests that patch a
    provider, harmful in prod — so we log a warning).
    """
    import logging

    log = logging.getLogger(__name__)
    if provider.kind in _REGISTRY:
        log.warning(
            "channel provider %r re-registered; last writer wins",
            provider.kind,
        )
    _REGISTRY[provider.kind] = provider
    return provider


def get_provider(kind: str) -> ChannelProvider:
    """Return the provider for ``kind``; raise KeyError if unknown.

    The ingress route catches KeyError and translates it into a 400 with
    ``code=channels.unknown_kind`` so a channel row pointing at an
    unloaded provider fails fast rather than silently 500ing.
    """
    p = _REGISTRY.get(kind)
    if p is None:
        raise KeyError(f"unknown channel kind: {kind}")
    return p


def describe_providers() -> list[dict]:
    """JSON-ready enumeration of every registered provider.

    Drives ``GET /api/v1/channels/kinds``. Frontend channel-create
    form iterates this to render the kind picker and the per-kind
    config-field editor.
    """
    out: list[dict] = []
    for kind in sorted(_REGISTRY.keys()):
        p = _REGISTRY[kind]
        meta = p.metadata()
        out.append(
            {
                "kind": kind,
                "display_name": meta.display_name,
                "description": meta.description,
                "docs_url": meta.docs_url,
                "required_config_fields": list(meta.required_config_fields),
                "optional_config_fields": list(meta.optional_config_fields),
                "supports_outbound": meta.supports_outbound,
            }
        )
    return out


def available_kinds() -> list[str]:
    return sorted(_REGISTRY.keys())


# ── Bundled providers — importing them runs register_provider(). ──
from app.services.channels.dingtalk import DingTalkProvider  # noqa: E402
from app.services.channels.discord import DiscordProvider  # noqa: E402
from app.services.channels.feishu import FeishuProvider  # noqa: E402
from app.services.channels.generic import WebhookProvider  # noqa: E402
from app.services.channels.slack import SlackProvider  # noqa: E402
from app.services.channels.teams import TeamsProvider  # noqa: E402
from app.services.channels.telegram import TelegramProvider  # noqa: E402
from app.services.channels.wecom import WeComProvider  # noqa: E402

register_provider(SlackProvider())
register_provider(FeishuProvider())
register_provider(DiscordProvider())
register_provider(WebhookProvider())
register_provider(DingTalkProvider())
register_provider(WeComProvider())
register_provider(TeamsProvider())
register_provider(TelegramProvider())


__all__ = [
    "ChannelProvider",
    "ChannelProviderMeta",
    "InboundMessage",
    "SignatureInvalid",
    "available_kinds",
    "describe_providers",
    "get_provider",
    "register_provider",
]
