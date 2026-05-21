"""Unit: shape invariants of the notification event registry (M0.10).

Pure-Python static checks. No DB, no Redis. The registry is the
contract between every audit-emitting call site and the fan-out;
breaking any of these invariants almost certainly means a wire-up
bug rather than an intentional change.
"""

from __future__ import annotations

import pytest

from app.services.notification_events import (
    EVENT_REGISTRY,
    EventDescriptor,
    NotificationChannel,
    NotificationUrgency,
    get_user_visible_event_keys,
)

_VALID_AUDIENCES = {
    "actor",
    "owner",
    "workspace_admins",
    "platform_admins",
    "broadcast",
}


def test_registry_has_all_required_keys():
    """M0.10 shipped 13 keys; M0.13 added ``platform_settings.changed``;
    a sibling M2.5.x added ``subagent.zombie_detected``; M2.5.3 added
    ``provider.cooldown_admin_alert``; M2.5.9 added
    ``cache.adaptive_disabled``.

    New milestones MUST extend this set rather than rename the test —
    the registry is the contract between audit-emitting sites and the
    fan-out.
    """
    expected = {
        "goal.alignment_low",
        "goal.locked",
        "goal.unlocked",
        "judge.score_negative",
        "judge.degraded",
        "channel.sender_blocked",
        "security.signature_failed",
        "auth.workspace_provisioned",
        "workspace.quota_exceeded",
        "workspace.spike_detected",
        "workspace.quota_increased",
        "job.failed_permanent",
        "approval.expiring",
        "platform_settings.changed",
        "subagent.zombie_detected",
        "inflight_run.lost_detected",
        "inflight_run.force_recycled",
        "provider.cooldown_admin_alert",
        "cache.adaptive_disabled",
    }
    assert set(EVENT_REGISTRY.keys()) == expected


@pytest.mark.parametrize("key", list(EVENT_REGISTRY.keys()))
def test_descriptor_shape_is_valid(key: str):
    desc = EVENT_REGISTRY[key]
    assert isinstance(desc, EventDescriptor)
    assert desc.key == key
    assert desc.target_audience in _VALID_AUDIENCES
    assert isinstance(desc.default_urgency, NotificationUrgency)
    assert desc.cooldown_seconds >= 0
    assert isinstance(desc.requires_email, bool)
    assert desc.title_key.startswith("notification.")
    assert desc.message_key.startswith("notification.")
    assert len(desc.default_channels) >= 1
    for c in desc.default_channels:
        assert isinstance(c, NotificationChannel)


@pytest.mark.parametrize("key", list(EVENT_REGISTRY.keys()))
def test_requires_email_implies_email_default_channel(key: str):
    """``requires_email=True`` events must keep EMAIL in their default channels.

    The fan-out re-injects EMAIL even if a user opted out, so
    consistency between the descriptor and the enforcement matters
    for the security guarantee ("you cannot turn off the welcome
    email").
    """
    desc = EVENT_REGISTRY[key]
    if desc.requires_email:
        assert NotificationChannel.EMAIL in desc.default_channels


def test_security_signature_failed_never_dedups():
    """Security event must always notify; cooldown_seconds == 0."""
    desc = EVENT_REGISTRY["security.signature_failed"]
    assert desc.cooldown_seconds == 0
    assert desc.requires_email is True
    assert desc.default_urgency == NotificationUrgency.CRITICAL


def test_user_visible_event_keys_filters_platform_admins():
    """``platform_admins`` audience events are admin-only — UI hides them."""
    user_visible = set(get_user_visible_event_keys())
    for key, desc in EVENT_REGISTRY.items():
        if desc.target_audience == "platform_admins":
            assert key not in user_visible
        else:
            assert key in user_visible


def test_workspace_spike_detected_uses_admin_audience():
    """Spike notifications must reach platform admins, not the noisy actor."""
    desc = EVENT_REGISTRY["workspace.spike_detected"]
    assert desc.target_audience == "platform_admins"
    assert desc.cooldown_seconds == 1800
