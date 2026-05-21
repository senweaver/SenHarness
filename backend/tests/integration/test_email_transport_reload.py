"""SMTP toggle in admin settings swaps the process-wide email transport."""

from __future__ import annotations

import pytest

from app.services import email_transport
from app.services import platform_settings as ps
from app.services.email_transport import (
    LogEmailTransport,
    SmtpEmailTransport,
    reload_email_transport_from_settings,
)
from app.services.platform_settings import PlatformSettingsSection

pytestmark = pytest.mark.asyncio


async def test_log_transport_when_smtp_disabled(db_session):
    ps.invalidate_local()
    # Reset to defaults to ensure ``enabled=False``.
    await ps._delete_section_rows(
        db_session, section=PlatformSettingsSection.EMAIL_SMTP
    )
    await db_session.commit()
    kind = await reload_email_transport_from_settings(db_session)
    assert kind == "log"
    assert isinstance(email_transport.get_email_transport(), LogEmailTransport)


async def test_smtp_transport_swap_in(db_session, identity):
    """Enabling SMTP via the admin path should swap the singleton."""
    ps.invalidate_local()
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.EMAIL_SMTP,
        payload={
            "enabled": True,
            "host": "smtp.example.com",
            "port": 2525,
            "username": "test",
            "password_ref": None,
            "from_address": "ops@example.com",
            "use_tls": False,
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    kind = await reload_email_transport_from_settings(db_session)
    assert kind == "smtp"
    assert isinstance(email_transport.get_email_transport(), SmtpEmailTransport)


async def test_smtp_transport_falls_back_when_required_field_missing(
    db_session, identity
):
    ps.invalidate_local()
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.EMAIL_SMTP,
        payload={
            "enabled": True,
            # host omitted on purpose
            "port": 587,
            "from_address": "ops@example.com",
            "use_tls": True,
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    kind = await reload_email_transport_from_settings(db_session)
    assert kind == "log"
