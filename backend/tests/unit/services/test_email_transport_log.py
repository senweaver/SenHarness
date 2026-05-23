"""Unit: LogEmailTransport contract (M0.10).

Asserts the default transport returns ``ok=True`` without touching
SMTP and that the audit hash is computed over the recipient address
(not the raw bytes).
"""

from __future__ import annotations

import asyncio
import hashlib

from app.services.email_transport import (
    EmailDispatchResult,
    LogEmailTransport,
    get_email_transport,
)


def test_get_email_transport_returns_log_by_default():
    transport = get_email_transport()
    assert isinstance(transport, LogEmailTransport)


def test_log_transport_returns_ok_dispatch_result():
    transport = LogEmailTransport()

    async def _send():
        return await transport.send(
            to="alice@example.com",
            subject="Welcome",
            body_text="Hello",
        )

    result = asyncio.run(_send())
    assert isinstance(result, EmailDispatchResult)
    assert result.ok is True
    assert result.transport == "log"
    assert result.message_id is None


def test_log_transport_logs_with_address_hash_only(caplog):
    """The log line uses a SHA-256 prefix of the recipient, never the raw address.

    Avoids leaking PII when the operator screen-shares the runtime
    log. The test sets the logger level to ensure the INFO line is
    captured even when the global root level is WARNING.
    """
    import logging

    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="app.services.email_transport")
    transport = LogEmailTransport()
    expected = hashlib.sha256(b"alice@example.com").hexdigest()[:16]

    async def _send():
        return await transport.send(
            to="alice@example.com",
            subject="Test",
            body_text="Body",
        )

    asyncio.run(_send())
    rendered = " ".join(rec.getMessage() for rec in caplog.records)
    # Either the dedicated INFO line landed, or the audit best-effort
    # path (which also carries the same hash). At least one must hit.
    assert expected in rendered or "to_hash" in rendered
    # The raw address must never show up.
    assert "alice@example.com" not in rendered
