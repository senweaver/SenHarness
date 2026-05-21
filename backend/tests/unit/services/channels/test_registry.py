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
        """Every bundled provider must resolve — this is the bar any
        deployment meets out of the box. The set covers global IM
        platforms (Slack / Discord / Teams / Telegram) plus the
        Chinese-market trio (Feishu+Lark / WeCom+WeChat / DingTalk +
        QQ Bot) and the generic webhook fallback."""
        kinds = set(available_kinds())
        assert {
            "slack",
            "feishu",
            "lark",
            "discord",
            "webhook",
            "dingtalk",
            "wecom",
            "wechat",
            "teams",
            "telegram",
            "qq",
        } <= kinds

    def test_register_and_lookup(self):
        register_provider(_FakeProvider())
        p = get_provider("_test_fake")
        assert p.kind == "_test_fake"

    def test_legacy_generic_webhook_alias(self):
        p = get_provider("generic_webhook")
        assert p.kind == "webhook"

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

    def test_lark_required_fields(self):
        rows = describe_providers()
        lark = next(r for r in rows if r["kind"] == "lark")
        for field in ("app_id", "app_secret", "verification_token"):
            assert field in lark["required_config_fields"]
        assert lark["supports_outbound"] is True

    def test_wechat_required_fields(self):
        """The ``wechat`` kind is the iLink Bot variant (personal
        WeChat via QR-login Bearer token), not the public-account
        protocol — which means stream mode requires zero fields
        upfront (the QR-login flow writes ``bot_token`` back into
        ``config_json`` after a successful scan), while the relay
        webhook fallback still asks for the token. Locking the
        per-mode shape so we don't accidentally regress to the MP
        protocol or to a bot_token-required upfront form."""
        rows = describe_providers()
        wechat = next(r for r in rows if r["kind"] == "wechat")
        assert wechat["required_config_fields"] == []
        assert "bot_token" in wechat["optional_config_fields"]
        assert wechat["mode_required_fields"] == {
            "stream": [],
            "webhook": ["bot_token"],
        }
        assert "iLink" in wechat["display_name"]
        assert wechat["supports_outbound"] is True

    def test_qq_required_fields(self):
        rows = describe_providers()
        qq = next(r for r in rows if r["kind"] == "qq")
        for field in ("app_id", "app_secret"):
            assert field in qq["required_config_fields"]
        assert qq["supports_outbound"] is True

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


class TestModeRequiredFields:
    """The Channel-create form renders only the fields the active
    transport actually needs. These tests pin each dual-mode provider's
    minimum config so a future copy-paste tweak in one provider can't
    silently force every operator to suddenly fill an extra secret."""

    def test_feishu_stream_drops_verification_token(self):
        rows = describe_providers()
        feishu = next(r for r in rows if r["kind"] == "feishu")
        assert feishu["mode_required_fields"]["stream"] == ["app_id", "app_secret"]
        assert feishu["mode_required_fields"]["webhook"] == [
            "app_id",
            "app_secret",
            "verification_token",
        ]
        assert feishu["mode_optional_fields"]["stream"] == []
        assert "encrypt_key" in feishu["mode_optional_fields"]["webhook"]

    def test_lark_mirrors_feishu(self):
        rows = describe_providers()
        lark = next(r for r in rows if r["kind"] == "lark")
        assert lark["mode_required_fields"]["stream"] == ["app_id", "app_secret"]
        assert lark["mode_required_fields"]["webhook"] == [
            "app_id",
            "app_secret",
            "verification_token",
        ]

    def test_dingtalk_two_unrelated_paths(self):
        """Stream mode uses ``client_id``/``client_secret`` (the OPEN-API
        app credentials) — webhook mode uses the custom-robot
        ``webhook_url`` + ``sign_secret`` pair. The form should swap
        the entire field set when the operator toggles modes."""
        rows = describe_providers()
        dt = next(r for r in rows if r["kind"] == "dingtalk")
        assert dt["mode_required_fields"]["stream"] == ["client_id", "client_secret"]
        assert dt["mode_required_fields"]["webhook"] == ["webhook_url", "sign_secret"]
        # Pin per-mode optional sets so the global ``optional_config_fields``
        # fallback can't ever render ``client_id`` / ``client_secret`` a
        # second time inside stream mode (a real bug the form hit before
        # ``mode_optional_fields`` was filled in for DingTalk).
        assert dt["mode_optional_fields"]["stream"] == []
        assert "client_id" not in dt["mode_optional_fields"]["stream"]
        assert "client_secret" not in dt["mode_optional_fields"]["stream"]
        assert "verify_signatures" in dt["mode_optional_fields"]["webhook"]

    def test_wecom_aibot_vs_self_built(self):
        rows = describe_providers()
        wc = next(r for r in rows if r["kind"] == "wecom")
        assert wc["mode_required_fields"]["stream"] == ["bot_id", "bot_secret"]
        assert wc["mode_required_fields"]["webhook"] == [
            "corp_id",
            "agent_id",
            "secret",
            "token",
            "encoding_aes_key",
        ]

    def test_discord_stream_drops_public_key(self):
        rows = describe_providers()
        dc = next(r for r in rows if r["kind"] == "discord")
        assert dc["mode_required_fields"]["stream"] == ["bot_token"]
        assert dc["mode_required_fields"]["webhook"] == ["bot_token", "public_key"]
        assert "application_id" in dc["mode_optional_fields"]["webhook"]

    def test_qq_same_pair_both_modes(self):
        rows = describe_providers()
        qq = next(r for r in rows if r["kind"] == "qq")
        assert qq["mode_required_fields"]["stream"] == ["app_id", "app_secret"]
        assert qq["mode_required_fields"]["webhook"] == ["app_id", "app_secret"]

    def test_single_mode_providers_emit_none(self):
        """Slack / Teams / Telegram / generic webhook are webhook-only
        and don't need per-mode splits — the metadata stays ``None`` and
        the frontend falls back to the global required/optional lists.
        """
        rows = describe_providers()
        for kind in ("slack", "teams", "telegram", "webhook"):
            row = next(r for r in rows if r["kind"] == kind)
            assert row["mode_required_fields"] is None
            assert row["mode_optional_fields"] is None
