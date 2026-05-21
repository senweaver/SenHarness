"""M0.8 — sender allowlist gate (``Channel.sender_allowlist_json``)."""

from __future__ import annotations

from app.services.channels._sender_filter import is_known_mode, is_sender_allowed


def test_default_mode_allow_all_lets_anyone_through() -> None:
    assert is_sender_allowed({}, "U-anyone")
    assert is_sender_allowed(None, "U-other")
    assert is_known_mode({})


def test_explicit_allow_all_passes() -> None:
    assert is_sender_allowed({"mode": "allow_all"}, "U-1")


def test_allow_listed_match() -> None:
    rules = {"mode": "allow_listed", "allow": ["U-1", "U-2"]}
    assert is_sender_allowed(rules, "U-1")
    assert not is_sender_allowed(rules, "U-99")
    assert is_known_mode(rules)


def test_allow_listed_blocks_anonymous() -> None:
    rules = {"mode": "allow_listed", "allow": []}
    assert not is_sender_allowed(rules, "U-1")
    assert not is_sender_allowed(rules, None)


def test_deny_listed_blocks_listed_only() -> None:
    rules = {"mode": "deny_listed", "deny": ["U-bad"]}
    assert not is_sender_allowed(rules, "U-bad")
    assert is_sender_allowed(rules, "U-fine")


def test_unknown_mode_fails_open_but_is_flagged() -> None:
    rules = {"mode": "purge_listed"}
    assert is_sender_allowed(rules, "U-anyone")
    assert not is_known_mode(rules)


def test_blank_sender_treated_as_no_match_for_allow_listed() -> None:
    rules = {"mode": "allow_listed", "allow": ["", "  "]}
    assert not is_sender_allowed(rules, "")
