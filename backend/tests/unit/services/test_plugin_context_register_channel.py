"""Unit tests for the M3.5 PluginContext extensions.

Cover the contract for the two new registration surfaces:

* ``register_channel_kind`` — installs a fresh channel provider,
  refuses to override builtin kinds, refuses repeated registration
  of the same plugin kind.
* ``register_model_provider`` — same shape, against the model
  provider catalog.

The plugin-host side (``plugin_host.register_hook``) keeps its
M2.5.5 contract; we only verify the new branches here.
"""

from __future__ import annotations

import pytest

from app.services.plugin_loader import PluginContext, PluginManifest


def _manifest(*scopes: str) -> PluginManifest:
    return PluginManifest(
        name="alpha",
        version="0.0.1",
        description="test",
        capability_scopes=tuple(scopes),
        entry_module="alpha.entry",
    )


# ── Channel registration ────────────────────────────────────
def test_register_channel_kind_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.channels.base import ChannelProvider

    class _PluginChannel(ChannelProvider):
        kind = "test_channel_unique"

        async def parse_inbound(self, _payload, _headers):  # pragma: no cover
            return None

    captured: list[tuple[str, ChannelProvider]] = []

    def _stub_register(kind: str, provider: ChannelProvider) -> None:
        captured.append((kind, provider))

    import app.services.channels as ch_mod

    monkeypatch.setattr(ch_mod, "register_provider_from_plugin", _stub_register)

    ctx = PluginContext(manifest=_manifest("register_channel"))
    ctx.register_channel_kind("test_channel_unique", _PluginChannel)

    assert len(captured) == 1
    kind, provider = captured[0]
    assert kind == "test_channel_unique"
    assert isinstance(provider, _PluginChannel)
    assert ctx.channels_registered == ("test_channel_unique",)


def test_register_channel_kind_refuses_without_scope() -> None:
    from app.services.channels.base import ChannelProvider

    class _PluginChannel(ChannelProvider):
        kind = "x"

        async def parse_inbound(self, _payload, _headers):  # pragma: no cover
            return None

    ctx = PluginContext(manifest=_manifest("pre_tool_call"))
    with pytest.raises(ValueError) as exc:
        ctx.register_channel_kind("x", _PluginChannel)
    assert "register_channel" in str(exc.value)


def test_register_channel_kind_refuses_builtin_kind() -> None:
    """A plugin can never override a bundled channel kind."""
    from app.services.channels.base import ChannelProvider

    class _Override(ChannelProvider):
        kind = "slack"

        async def parse_inbound(self, _payload, _headers):  # pragma: no cover
            return None

    ctx = PluginContext(manifest=_manifest("register_channel"))
    with pytest.raises(ValueError) as exc:
        ctx.register_channel_kind("slack", _Override)
    assert "builtin" in str(exc.value).lower()


def test_register_channel_kind_refuses_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.channels as ch_mod
    from app.services.channels.base import ChannelProvider

    # Reset plugin-registered set so the test is independent of order.
    monkeypatch.setattr(ch_mod, "_PLUGIN_REGISTERED_KINDS", set())

    class _Channel(ChannelProvider):
        kind = "duplicate_channel"

        async def parse_inbound(self, _payload, _headers):  # pragma: no cover
            return None

    ctx = PluginContext(manifest=_manifest("register_channel"))
    ctx.register_channel_kind("duplicate_channel", _Channel)
    with pytest.raises(ValueError) as exc:
        ctx.register_channel_kind("duplicate_channel", _Channel)
    assert "already registered" in str(exc.value).lower()


def test_register_channel_kind_refuses_kind_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory-returned ``provider.kind`` must match the kind argument."""
    import app.services.channels as ch_mod
    from app.services.channels.base import ChannelProvider

    monkeypatch.setattr(ch_mod, "_PLUGIN_REGISTERED_KINDS", set())

    class _MismatchChannel(ChannelProvider):
        kind = "actually_a_different_kind"

        async def parse_inbound(self, _payload, _headers):  # pragma: no cover
            return None

    ctx = PluginContext(manifest=_manifest("register_channel"))
    with pytest.raises(ValueError) as exc:
        ctx.register_channel_kind("expected_kind", _MismatchChannel)
    assert "mismatch" in str(exc.value).lower()


# ── Model provider registration ─────────────────────────────
def test_register_model_provider_happy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.kernels.provider_catalog as cat_mod
    from app.agents.kernels.provider_catalog import CatalogEntry

    monkeypatch.setattr(cat_mod, "_PLUGIN_REGISTERED_KINDS", set())

    entry = CatalogEntry(
        kind="test_provider_unique",
        display_name="Test",
        display_name_zh="测试",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="test",
        description_zh="测试",
    )

    ctx = PluginContext(manifest=_manifest("register_model_provider"))
    ctx.register_model_provider("test_provider_unique", lambda: entry)

    assert ctx.providers_registered == ("test_provider_unique",)
    assert cat_mod.is_plugin_kind("test_provider_unique") is True


def test_register_model_provider_refuses_without_scope() -> None:
    from app.agents.kernels.provider_catalog import CatalogEntry

    entry = CatalogEntry(
        kind="x",
        display_name="Test",
        display_name_zh="测试",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="",
        description_zh="",
    )
    ctx = PluginContext(manifest=_manifest("pre_tool_call"))
    with pytest.raises(ValueError) as exc:
        ctx.register_model_provider("x", lambda: entry)
    assert "register_model_provider" in str(exc.value)


def test_register_model_provider_refuses_builtin_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.kernels.provider_catalog import CatalogEntry

    entry = CatalogEntry(
        kind="openai",
        display_name="x",
        display_name_zh="x",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="",
        description_zh="",
    )
    ctx = PluginContext(manifest=_manifest("register_model_provider"))
    with pytest.raises(ValueError) as exc:
        ctx.register_model_provider("openai", lambda: entry)
    assert "builtin" in str(exc.value).lower()


def test_register_tool_requires_scope() -> None:
    """``register_tool`` is gated by an explicit capability scope."""
    ctx = PluginContext(manifest=_manifest("pre_tool_call"))
    with pytest.raises(ValueError) as exc:
        ctx.register_tool("test.tool", args_model=None, runner=lambda **_: None)
    assert "register_tool" in str(exc.value)
