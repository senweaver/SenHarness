"""Pure helpers in :mod:`app.services.session_user_prefs`.

The DB-backed read/write paths are covered by integration tests; here we
exercise the input-coercion guard that protects the JSON column from
garbage values forwarded by the frontend.
"""

from __future__ import annotations

from app.services.session_user_prefs import _coerce_pref


def test_valid_provider_colon_model_passes_through() -> None:
    assert _coerce_pref("openai:gpt-4o-mini") == "openai:gpt-4o-mini"
    assert _coerce_pref("deepseek:deepseek-chat") == "deepseek:deepseek-chat"
    assert _coerce_pref("openrouter:openai/gpt-4o") == "openrouter:openai/gpt-4o"


def test_strips_outer_whitespace() -> None:
    assert _coerce_pref("  openai:gpt-4o  ") == "openai:gpt-4o"


def test_rejects_missing_separator() -> None:
    """No colon → cannot be parsed by ``parse_override`` → drop silently."""
    assert _coerce_pref("gpt-4o") is None
    assert _coerce_pref("just-a-model") is None


def test_rejects_non_string() -> None:
    assert _coerce_pref(None) is None
    assert _coerce_pref(123) is None  # type: ignore[arg-type]
    assert _coerce_pref(["openai", "gpt-4o"]) is None  # type: ignore[arg-type]


def test_rejects_oversized_value() -> None:
    """Defensive cap so a malicious caller can't bloat ``profile_json``."""
    big = "openai:" + ("a" * 300)
    assert _coerce_pref(big) is None
