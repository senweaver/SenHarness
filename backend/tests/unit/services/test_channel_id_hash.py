"""M0.8 — ``compute_external_app_id_hash`` per-kind stability."""

from __future__ import annotations

from app.services.channels._id_hash import compute_external_app_id_hash


def test_discord_hash_stable_for_same_token() -> None:
    a = compute_external_app_id_hash("discord", {"bot_token": "abc"})
    b = compute_external_app_id_hash("discord", {"bot_token": "abc"})
    assert a == b


def test_discord_hash_changes_with_token() -> None:
    a = compute_external_app_id_hash("discord", {"bot_token": "abc"})
    b = compute_external_app_id_hash("discord", {"bot_token": "xyz"})
    assert a != b


def test_same_token_in_different_kinds_does_not_collide() -> None:
    discord = compute_external_app_id_hash("discord", {"bot_token": "abc"})
    telegram = compute_external_app_id_hash("telegram", {"bot_token": "abc"})
    assert discord != telegram


def test_slack_hash_uses_signing_secret() -> None:
    a = compute_external_app_id_hash("slack", {"signing_secret": "shh", "bot_token": "x"})
    b = compute_external_app_id_hash("slack", {"signing_secret": "shh", "bot_token": "y"})
    assert a == b
    c = compute_external_app_id_hash("slack", {"signing_secret": "different"})
    assert a != c


def test_lark_combines_app_id_and_secret() -> None:
    a = compute_external_app_id_hash("lark", {"app_id": "id", "app_secret": "s1"})
    b = compute_external_app_id_hash("lark", {"app_id": "id", "app_secret": "s2"})
    assert a != b


def test_dingtalk_falls_back_to_app_key_when_client_id_missing() -> None:
    a = compute_external_app_id_hash("dingtalk", {"client_id": "cid"})
    b = compute_external_app_id_hash("dingtalk", {"app_key": "cid"})
    assert a == b


def test_wechat_uses_bot_token_when_app_id_unset() -> None:
    a = compute_external_app_id_hash("wechat", {"bot_token": "tok"})
    b = compute_external_app_id_hash("wechat", {"app_id": "tok"})
    assert a == b


def test_generic_webhook_returns_none() -> None:
    assert compute_external_app_id_hash("webhook", {"hmac_secret": "shh"}) is None
    assert compute_external_app_id_hash("generic_webhook", {"hmac_secret": "shh"}) is None


def test_unknown_kind_returns_none() -> None:
    assert compute_external_app_id_hash("__unknown__", {"bot_token": "x"}) is None


def test_empty_inputs_return_none() -> None:
    assert compute_external_app_id_hash("discord", {}) is None
    assert compute_external_app_id_hash("discord", {"bot_token": ""}) is None
    assert compute_external_app_id_hash("discord", {"bot_token": "   "}) is None
