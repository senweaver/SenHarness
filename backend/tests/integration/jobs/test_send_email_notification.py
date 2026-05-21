"""Integration: ``send_email_notification`` ARQ task happy + failure path.

The real DB / Redis fixtures are not strictly needed because the
default :class:`LogEmailTransport` is in-process. We swap it for a
deterministic stub via :func:`set_email_transport` so the failure
case is reproducible without touching SMTP.
"""

from __future__ import annotations

import asyncio

import pytest

from app.jobs.notification import (
    on_notification_job_failed_permanent,
    send_email_notification,
)
from app.services.email_transport import (
    EmailDispatchResult,
    LogEmailTransport,
    get_email_transport,
    set_email_transport,
)


@pytest.fixture(autouse=True)
def _reset_transport():
    """Restore the default transport between tests."""
    yield
    set_email_transport(LogEmailTransport())


async def test_log_transport_returns_sent_status():
    """The default transport returns ok and the job reports ``sent``."""
    payload = {
        "event_key": "auth.workspace_provisioned",
        "to_email": "user@example.com",
        "title_key": "notification.workspaceProvisioned.title",
        "message_key": "notification.workspaceProvisioned.message",
        "payload": {"workspace_slug": "demo"},
        "urgency": "info",
        "workspace_id": None,
        "idempotency_key": "k" * 32,
        "subject_fallback": "Welcome",
        "body_fallback": "Body",
    }
    out = await send_email_notification({}, payload)
    assert out["status"] == "sent"
    assert out["transport"] == "log"


async def test_failing_transport_raises_and_terminal_failure_audits():
    """Three failures trigger the worker hook → ``job.failed_permanent`` audit."""

    class _BoomTransport:
        async def send(self, **kwargs):
            return EmailDispatchResult(
                ok=False, transport="boom", message_id=None, error="kaboom"
            )

    set_email_transport(_BoomTransport())

    payload = {
        "event_key": "channel.sender_blocked",
        "to_email": "ops@example.com",
        "title_key": "notification.channelSenderBlocked.title",
        "message_key": "notification.channelSenderBlocked.message",
        "payload": {"channel_id": "x"},
        "urgency": "warn",
        "subject_fallback": "blocked",
        "body_fallback": "body",
        "idempotency_key": "x" * 32,
    }
    with pytest.raises(RuntimeError):
        await send_email_notification({}, payload)

    # Simulate the worker dispatching the permanent-failure hook after 3 strikes.
    captured: list[dict] = []

    async def _record_stub(db, **kwargs):
        captured.append(kwargs)

    import app.services.audit as audit_svc

    original = audit_svc.record
    audit_svc.record = _record_stub  # type: ignore[assignment]
    try:
        await on_notification_job_failed_permanent(
            {
                "function": "send_email_notification",
                "args": [payload],
                "job_id": "abc123",
            },
            RuntimeError("kaboom"),
        )
    finally:
        audit_svc.record = original  # type: ignore[assignment]

    actions = [c.get("action") for c in captured]
    assert "job.failed_permanent" in actions


async def test_empty_to_email_skips_transport():
    """Missing recipient must not raise — the audit covers the gap."""
    payload = {
        "event_key": "judge.degraded",
        "to_email": "",
        "title_key": "notification.judgeDegraded.title",
        "message_key": "notification.judgeDegraded.message",
        "payload": {},
        "urgency": "warn",
        "idempotency_key": "z" * 32,
    }
    out = await send_email_notification({}, payload)
    assert out["status"] == "skipped_no_address"


def test_default_transport_is_log_transport():
    assert isinstance(get_email_transport(), LogEmailTransport)


_ = asyncio
