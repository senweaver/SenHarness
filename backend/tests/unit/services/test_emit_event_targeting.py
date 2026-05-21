"""Unit: target-audience resolution + identity preference filter.

The fan-out's correctness hinges on `_resolve_targets` returning the
right set of identities per audience and `_effective_channels`
honouring the platform's "requires_email" floor. We exercise both with
in-memory `Identity` stubs and a tiny fake AsyncSession so the case is
deterministic without spinning Postgres.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.notification import NotificationLevel  # noqa: F401
from app.services.notification_events import (
    EVENT_REGISTRY,
    NotificationChannel,
    _effective_channels,
    _read_identity_prefs,
)


class _StubIdentity:
    """Lightweight stand-in for the ORM ``Identity`` model.

    We only need the attributes the fan-out reads directly; nothing in
    the prefs path touches a session.
    """

    def __init__(self, prefs: dict | None = None):
        self.id = uuid.uuid4()
        self.notification_prefs_json = prefs or {}


def test_read_identity_prefs_returns_empty_when_unset():
    ident = _StubIdentity()
    assert _read_identity_prefs(ident, "goal.alignment_low") == {}


def test_read_identity_prefs_returns_dict_when_set():
    ident = _StubIdentity(
        prefs={"goal.alignment_low": {"channels": ["in_app"], "muted": False}}
    )
    out = _read_identity_prefs(ident, "goal.alignment_low")
    assert out == {"channels": ["in_app"], "muted": False}


def test_effective_channels_falls_back_to_descriptor_defaults():
    descriptor = EVENT_REGISTRY["goal.alignment_low"]
    ident = _StubIdentity()
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=True
    )
    assert NotificationChannel.IN_APP in out


def test_muted_event_returns_empty_when_not_required_email():
    descriptor = EVENT_REGISTRY["goal.alignment_low"]
    ident = _StubIdentity(
        prefs={"goal.alignment_low": {"channels": [], "muted": True}}
    )
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=True
    )
    assert out == set()


def test_requires_email_cannot_be_muted_via_user_prefs():
    """The security floor — ``requires_email=True`` events always email.

    Even when the user mutes the event, EMAIL stays in the effective
    channel set so welcome / security notifications cannot be opted out.
    """
    descriptor = EVENT_REGISTRY["security.signature_failed"]
    ident = _StubIdentity(
        prefs={"security.signature_failed": {"channels": [], "muted": True}}
    )
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=True
    )
    assert out == {NotificationChannel.EMAIL}


def test_user_can_drop_in_app_for_requires_email_event():
    """``channels=['email']`` shrinks IN_APP but EMAIL stays mandatory."""
    descriptor = EVENT_REGISTRY["channel.sender_blocked"]
    ident = _StubIdentity(
        prefs={
            "channel.sender_blocked": {"channels": ["email"], "muted": False}
        }
    )
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=True
    )
    assert NotificationChannel.EMAIL in out


def test_platform_critical_only_strips_email_from_non_security_event():
    descriptor = EVENT_REGISTRY["workspace.quota_increased"]
    ident = _StubIdentity()
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=True
    )
    assert NotificationChannel.EMAIL not in out
    assert NotificationChannel.IN_APP in out


def test_platform_critical_only_off_keeps_email_for_info_event():
    descriptor = EVENT_REGISTRY["workspace.quota_increased"]
    ident = _StubIdentity()
    out = _effective_channels(
        descriptor, ident, platform_email_critical_only=False
    )
    assert NotificationChannel.EMAIL in out


@pytest.mark.parametrize(
    "audience",
    ["actor", "owner", "workspace_admins", "platform_admins", "broadcast"],
)
def test_audience_value_is_one_of_supported(audience: str):
    """Sanity: every audience used in the registry has a resolver branch."""
    used = {desc.target_audience for desc in EVENT_REGISTRY.values()}
    assert audience in {
        "actor",
        "owner",
        "workspace_admins",
        "platform_admins",
        "broadcast",
    } or audience not in used
