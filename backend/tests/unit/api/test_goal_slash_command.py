"""Unit tests for the /goal slash command parser used by the WS handler.

We test the pure parser here (no WS / DB) so it stays testable without
spinning up Redis. The WS round-trip is exercised at integration level.
"""

from __future__ import annotations

from app.api.v1.sessions import _parse_goal_slash_command


def test_parse_status_bare():
    assert _parse_goal_slash_command("/goal") == ("status", "")
    assert _parse_goal_slash_command("  /goal  ") == ("status", "")
    assert _parse_goal_slash_command("/GOAL") == ("status", "")


def test_parse_unlock_variants():
    assert _parse_goal_slash_command("/goal unlock") == ("unlock", "")
    assert _parse_goal_slash_command("/Goal Unlock") == ("unlock", "")
    assert _parse_goal_slash_command("  /goal   unlock  ") == ("unlock", "")


def test_parse_lock_text():
    cmd = _parse_goal_slash_command("/goal Ship M0.1 by Friday")
    assert cmd == ("lock", "Ship M0.1 by Friday")


def test_parse_lock_multiline():
    text = "/goal Multi-line\ngoal text"
    assert _parse_goal_slash_command(text) == ("lock", "Multi-line\ngoal text")


def test_parse_non_goal_returns_none():
    assert _parse_goal_slash_command("Hello world") is None
    assert _parse_goal_slash_command("/insight last 7 days") is None
    assert _parse_goal_slash_command("") is None
    # Bare slash should not hijack the command — the chat composer may
    # surface a generic command palette.
    assert _parse_goal_slash_command("/") is None


def test_parse_lock_preserves_internal_whitespace():
    cmd = _parse_goal_slash_command("/goal   write the report   ")
    assert cmd == ("lock", "write the report")
