"""Pure-function coverage for ``_command_in_allowlist``.

The allowlist gate is the first check on the command path — it runs
before the approval flow so an obviously denied command never wakes up
an approver. Empty allowlist means "open" in dev environments;
production with ``execute=True`` is blocked at config-load time so
the empty-list path is unreachable in prod.
"""

from __future__ import annotations

import pytest

from app.services.sandbox_ssh import _command_in_allowlist


def test_empty_allowlist_lets_anything_through():
    assert _command_in_allowlist("rm -rf /", []) is True


def test_first_token_must_match_allowlist_entry():
    assert _command_in_allowlist("ls -la /var/log", ["ls", "uptime"]) is True
    assert _command_in_allowlist("uptime", ["ls", "uptime"]) is True


def test_command_not_in_allowlist_rejected():
    assert _command_in_allowlist("rm /tmp/foo", ["ls", "uptime"]) is False


def test_partial_substring_match_does_not_pass():
    assert _command_in_allowlist("/usr/bin/ls", ["ls"]) is False


def test_quoted_arguments_supported():
    assert (
        _command_in_allowlist('grep -R "needle" /etc', ["grep", "ls"]) is True
    )


def test_unbalanced_quotes_rejected_safely():
    """Unbalanced quotes can't be confidently lexed; reject rather than
    risk gating on the wrong token.
    """
    assert _command_in_allowlist('echo "abc', ["echo"]) is False


def test_empty_command_rejected():
    assert _command_in_allowlist("", ["ls"]) is False
    assert _command_in_allowlist("   ", ["ls"]) is False


@pytest.mark.parametrize(
    "command",
    ["ls -la", "ls", "ls /var/log", "  ls /tmp  "],
)
def test_leading_whitespace_does_not_break_matching(command):
    assert _command_in_allowlist(command, ["ls"]) is True


def test_pipe_or_chain_first_token_only():
    """``ls | rm`` would match ``ls``; that is the documented behaviour
    — operators who want shell-meta gating must add the chained
    interpreter (``bash``, ``sh``) to the allowlist explicitly. The
    test pins the contract so future changes surface here.
    """
    assert _command_in_allowlist("ls | rm -rf /", ["ls"]) is True
