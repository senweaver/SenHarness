"""Secret fields are masked before they reach the audit log."""

from __future__ import annotations

from app.services.platform_settings import _diff_payloads, _redact_for_audit


def test_redact_replaces_password_ref():
    redacted = _redact_for_audit({"host": "smtp.example.com", "password_ref": "smtp/main"})
    assert redacted["host"] == "smtp.example.com"
    assert redacted["password_ref"] == "***"


def test_redact_keeps_empty_secret_visible_for_diff_clarity():
    redacted = _redact_for_audit({"password_ref": None})
    assert redacted["password_ref"] is None


def test_redact_walks_nested_dicts():
    redacted = _redact_for_audit(
        {"providers": [{"name": "github", "client_secret_ref": "vault/key"}]}
    )
    assert redacted["providers"][0]["client_secret_ref"] == "***"
    assert redacted["providers"][0]["name"] == "github"


def test_diff_only_returns_changed_keys():
    old = {"a": 1, "b": "old"}
    new = {"a": 1, "b": "new"}
    assert _diff_payloads(old, new) == {"b": {"old": "old", "new": "new"}}
