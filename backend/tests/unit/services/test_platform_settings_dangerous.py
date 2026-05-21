"""Dangerous transitions are detected and flagged."""

from __future__ import annotations

from app.services.platform_settings import (
    PlatformSettingsSection,
    _detect_dangerous_changes,
)


def test_sandbox_false_to_true_is_dangerous():
    flagged = _detect_dangerous_changes(
        PlatformSettingsSection.SECURITY_SANDBOX,
        old={"allow_local_execute_in_prod": False},
        new={"allow_local_execute_in_prod": True},
    )
    assert flagged == ["allow_local_execute_in_prod"]


def test_sandbox_true_to_false_is_safe():
    flagged = _detect_dangerous_changes(
        PlatformSettingsSection.SECURITY_SANDBOX,
        old={"allow_local_execute_in_prod": True},
        new={"allow_local_execute_in_prod": False},
    )
    assert flagged == []


def test_registration_to_closed_is_dangerous():
    flagged = _detect_dangerous_changes(
        PlatformSettingsSection.AUTH_REGISTRATION,
        old={"mode": "open_personal"},
        new={"mode": "closed"},
    )
    assert flagged == ["mode"]


def test_registration_between_open_modes_is_safe():
    flagged = _detect_dangerous_changes(
        PlatformSettingsSection.AUTH_REGISTRATION,
        old={"mode": "open_personal"},
        new={"mode": "open_invite_only"},
    )
    assert flagged == []


def test_plugins_unauth_toggle_is_dangerous():
    flagged = _detect_dangerous_changes(
        PlatformSettingsSection.PLUGINS,
        old={"allow_user_plugins": False, "allow_unapproved_plugins": False},
        new={"allow_user_plugins": True, "allow_unapproved_plugins": False},
    )
    assert flagged == ["allow_user_plugins"]
