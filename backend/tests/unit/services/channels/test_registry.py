"""Tests for the channel provider registry.

The registry is the one place the frontend / ingress layer asks
"which channels can this deployment accept?". Breaking its shape
breaks the Channel-create form in lockstep, so these tests lock
the contract.
"""

from __future__ import annotations

import pytest

from app.services.channels import (
    available_kinds,
    describe_providers,
    get_provider,
    register_provider,
)
from app.services.channels.base import ChannelProvider, ChannelProviderMeta


class _FakeProvider(ChannelProvider):
    kind = "_test_fake"

    @classmethod
    def metadata(cls):
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Fake",
            description="unit test only",
            docs_url="",
            required_config_fields=("fake_secret",),
            optional_config_fields=("fake_optional",),
            supports_outbound=False,
        )

    def parse_inbound(self, payload, headers):
        return None


class TestRegistry:
    def test_bundled_providers_registered(self):
        """All six bundled providers must resolve — this is the bar
        any deployment meets out of the box."""
        kinds = set(available_kinds())
        assert {
            "slack",
            "feishu",
            "discord",
            "webhook",
            "dingtalk",
            "wecom",
            "teams",
            "telegram",
        } <= kinds

    def test_register_and_lookup(self):
        register_provider(_FakeProvider())
        p = get_provider("_test_fake")
        assert p.kind == "_test_fake"

    def test_unknown_kind_raises_key_error(self):
        with pytest.raises(KeyError):
            get_provider("definitely-unknown")


class TestDescribeShape:
    """The frontend Channel-create form reads every key below. Keep
    them stable or update both ends in lockstep."""

    def test_describe_has_ui_fields(self):
        register_provider(_FakeProvider())
        rows = describe_providers()
        for row in rows:
            assert set(row.keys()) >= {
                "kind",
                "display_name",
                "description",
                "docs_url",
                "required_config_fields",
                "optional_config_fields",
                "supports_outbound",
            }
            assert isinstance(row["required_config_fields"], list)
            assert isinstance(row["optional_config_fields"], list)
            assert isinstance(row["supports_outbound"], bool)

    def test_dingtalk_required_fields(self):
        rows = describe_providers()
        dt = next(r for r in rows if r["kind"] == "dingtalk")
        assert "webhook_url" in dt["required_config_fields"]
        assert "sign_secret" in dt["required_config_fields"]
        assert dt["supports_outbound"] is True

    def test_wecom_required_fields(self):
        rows = describe_providers()
        wc = next(r for r in rows if r["kind"] == "wecom")
        for field in (
            "corp_id",
            "agent_id",
            "secret",
            "token",
            "encoding_aes_key",
        ):
            assert field in wc["required_config_fields"]

    def test_teams_required_fields(self):
        rows = describe_providers()
        teams = next(r for r in rows if r["kind"] == "teams")
        assert "signing_secret" in teams["required_config_fields"]
        assert "incoming_webhook_url" in teams["optional_config_fields"]

    def test_telegram_required_fields(self):
        rows = describe_providers()
        telegram = next(r for r in rows if r["kind"] == "telegram")
        assert "bot_token" in telegram["required_config_fields"]
        assert "secret_token" in telegram["optional_config_fields"]

    def test_webhook_is_inbound_only(self):
        rows = describe_providers()
        wh = next(r for r in rows if r["kind"] == "webhook")
        assert wh["supports_outbound"] is False

    def test_rows_are_sorted_by_kind(self):
        """Stable ordering so the UI picker doesn't jitter between
        requests (we sort by ``kind`` inside describe_providers)."""
        rows = describe_providers()
        kinds = [r["kind"] for r in rows]
        assert kinds == sorted(kinds)
