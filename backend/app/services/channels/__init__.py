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

# Legacy ``channels.kind`` values kept in older DB rows / e2e fixtures.
_LEGACY_KIND_ALIASES: dict[str, str] = {
    "generic_webhook": "webhook",
}

# Channel kinds added by signed + approved plugins via
# ``PluginContext.register_channel_kind`` (M3.5). A built-in kind can
# never be overridden — plugin authors must pick a name outside this
# set, otherwise an inbound webhook's audit trail would become
# ambiguous about whether the built-in or the override handled it.
_BUILTIN_KINDS: set[str] = set()
_PLUGIN_REGISTERED_KINDS: set[str] = set()


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
    resolved = _LEGACY_KIND_ALIASES.get(kind, kind)
    p = _REGISTRY.get(resolved)
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

        def _fanout(
            overrides: dict[str, tuple[str, ...]] | None,
        ) -> dict[str, list[str]] | None:
            if overrides is None:
                return None
            return {mode: list(fields) for mode, fields in overrides.items()}

        out.append(
            {
                "kind": kind,
                "display_name": meta.display_name,
                "description": meta.description,
                "docs_url": meta.docs_url,
                "required_config_fields": list(meta.required_config_fields),
                "optional_config_fields": list(meta.optional_config_fields),
                "supports_outbound": meta.supports_outbound,
                "supported_modes": list(meta.supported_modes),
                "default_mode": meta.default_mode,
                "stream_requires_extra": meta.stream_requires_extra,
                # Realtime probe — False when the SDK is declared but the
                # extra wasn't installed. Frontend uses this to grey the
                # Mode toggle and surface a "pip install" hint.
                "stream_available": type(p).stream_available(),
                # Per-mode field overrides drive the "show only what this
                # mode needs" UX in the channel-create form. ``None``
                # means the form should use the global required/optional
                # lists for every mode (back-compat for community
                # adapters that don't bother).
                "mode_required_fields": _fanout(meta.mode_required_fields),
                "mode_optional_fields": _fanout(meta.mode_optional_fields),
                "mode_hidden_fields": _fanout(meta.mode_hidden_fields),
            }
        )
    return out


def available_kinds() -> list[str]:
    return sorted(_REGISTRY.keys())


def register_provider_from_plugin(kind: str, provider: ChannelProvider) -> None:
    """Register a plugin-contributed channel provider (M3.5).

    Refuses to replace any kind installed by the bundled providers
    below (the snapshot of ``_BUILTIN_KINDS`` taken at import time).
    The plugin must use a fresh kind string so audit / dispatch can
    deterministically attribute the provider.

    Re-registration of an already plugin-installed kind also raises
    so a buggy plugin reload doesn't silently swap out a live
    provider mid-flight.
    """
    if not isinstance(provider, ChannelProvider):
        raise TypeError(
            f"register_provider_from_plugin expected ChannelProvider; got {type(provider).__name__}"
        )
    if provider.kind != kind:
        raise ValueError(
            f"plugin channel provider mismatch: factory returned kind="
            f"{provider.kind!r}, register_channel_kind argument was {kind!r}"
        )
    if kind in _BUILTIN_KINDS:
        raise ValueError(f"plugin cannot override builtin channel kind: {kind!r}")
    if kind in _PLUGIN_REGISTERED_KINDS:
        raise ValueError(
            f"plugin channel kind {kind!r} already registered; reload the "
            "plugin via the admin console to install a fresh instance"
        )
    _PLUGIN_REGISTERED_KINDS.add(kind)
    register_provider(provider)


def is_plugin_kind(kind: str) -> bool:
    """Whether ``kind`` was contributed by a plugin (vs. bundled)."""
    return kind in _PLUGIN_REGISTERED_KINDS


# ── Bundled providers — importing them runs register_provider(). ──
from app.services.channels.dingtalk import DingTalkProvider
from app.services.channels.discord import DiscordProvider
from app.services.channels.feishu import FeishuProvider
from app.services.channels.generic import WebhookProvider
from app.services.channels.lark import LarkProvider
from app.services.channels.qq import QQBotProvider
from app.services.channels.slack import SlackProvider
from app.services.channels.teams import TeamsProvider
from app.services.channels.telegram import TelegramProvider
from app.services.channels.wechat import WeChatProvider
from app.services.channels.wecom import WeComProvider

register_provider(SlackProvider())
register_provider(FeishuProvider())
register_provider(LarkProvider())
register_provider(DiscordProvider())
register_provider(WebhookProvider())
register_provider(DingTalkProvider())
register_provider(WeComProvider())
register_provider(WeChatProvider())
register_provider(QQBotProvider())
register_provider(TeamsProvider())
register_provider(TelegramProvider())

# Snapshot the built-in kinds AFTER the bundled providers register
# but BEFORE any plugin loads. ``register_provider_from_plugin``
# checks against this set to refuse plugin overrides of bundled
# transports.
_BUILTIN_KINDS.update(_REGISTRY.keys())


__all__ = [
    "ChannelProvider",
    "ChannelProviderMeta",
    "InboundMessage",
    "SignatureInvalid",
    "available_kinds",
    "describe_providers",
    "get_provider",
    "is_plugin_kind",
    "register_provider",
    "register_provider_from_plugin",
]
