"""DB-backed checks for the section get / update / reset flow."""

from __future__ import annotations

import pytest

from app.services import platform_settings as ps
from app.services.platform_settings import (
    DangerousChangeRequiresConfirmation,
    PlatformSettingsSection,
)

pytestmark = pytest.mark.asyncio


async def test_get_section_returns_default_when_db_empty(db_session):
    ps.invalidate_local()
    value = await ps.get_section(
        db_session, section=PlatformSettingsSection.GENERAL
    )
    assert value.site_name


async def test_update_section_round_trips(db_session, identity):
    ps.invalidate_local()
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.GENERAL,
        payload={
            "site_name": "Acme",
            "primary_color_hex": "#112233",
            "default_locale": "zh-CN",
            "default_timezone": "Asia/Shanghai",
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    ps.invalidate_local()
    fresh = await ps.get_section(
        db_session, section=PlatformSettingsSection.GENERAL
    )
    assert fresh.site_name == "Acme"
    assert fresh.primary_color_hex == "#112233"


async def test_dangerous_change_requires_confirmation(db_session, identity):
    ps.invalidate_local()
    with pytest.raises(DangerousChangeRequiresConfirmation):
        await ps.update_section(
            db_session,
            section=PlatformSettingsSection.SECURITY_SANDBOX,
            payload={
                "allow_local_execute_in_prod": True,
                "allow_ssh_backend": False,
                "require_command_allowlist_in_prod": True,
            },
            actor_identity_id=identity.id,
            confirmed_dangerous=False,
        )


async def test_dangerous_change_passes_when_confirmed(db_session, identity):
    ps.invalidate_local()
    new = await ps.update_section(
        db_session,
        section=PlatformSettingsSection.SECURITY_SANDBOX,
        payload={
            "allow_local_execute_in_prod": True,
            "allow_ssh_backend": False,
            "require_command_allowlist_in_prod": True,
        },
        actor_identity_id=identity.id,
        confirmed_dangerous=True,
    )
    assert new.allow_local_execute_in_prod is True


async def test_reset_section_drops_db_row(db_session, identity):
    ps.invalidate_local()
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.WORKSPACE_DEFAULTS,
        payload={
            "branding_agent_term_default": "secretary",
            "new_workspace_default_model": None,
            "new_workspace_sandbox_kind": "docker",
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    meta = await ps.get_section_with_meta(
        db_session, section=PlatformSettingsSection.WORKSPACE_DEFAULTS
    )
    assert meta.db_present is True

    await ps.reset_section(
        db_session,
        section=PlatformSettingsSection.WORKSPACE_DEFAULTS,
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    meta = await ps.get_section_with_meta(
        db_session, section=PlatformSettingsSection.WORKSPACE_DEFAULTS
    )
    assert meta.db_present is False


async def test_auth_registration_round_trips_across_legacy_keys(
    db_session, identity
):
    """The aggregated section must read/write four legacy keys."""
    ps.invalidate_local()
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.AUTH_REGISTRATION,
        payload={
            "mode": "open_invite_only",
            "require_email_verification": True,
            "rate_limit_per_minute": 7,
            "invitation_expiry_days": 14,
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    ps.invalidate_local()
    value = await ps.get_section(
        db_session, section=PlatformSettingsSection.AUTH_REGISTRATION
    )
    assert value.mode == "open_invite_only"
    assert value.require_email_verification is True
    assert value.rate_limit_per_minute == 7
    assert value.invitation_expiry_days == 14


async def test_invalid_payload_raises_validation_failed(db_session, identity):
    from app.core.errors import ValidationFailed

    ps.invalidate_local()
    with pytest.raises(ValidationFailed):
        await ps.update_section(
            db_session,
            section=PlatformSettingsSection.GENERAL,
            payload={"primary_color_hex": "not-a-hex"},
            actor_identity_id=identity.id,
        )


async def test_unknown_section_raises(db_session, identity):
    from app.services.platform_settings import UnknownPlatformSection

    with pytest.raises(UnknownPlatformSection):
        await ps.get_section(db_session, section="not-a-real-section")


async def test_bootstrap_skips_already_populated_rows(
    db_session, identity, monkeypatch
):
    """Bootstrap must NOT overwrite a value the admin already set."""
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    ps.invalidate_local()
    # Explicit pre-existing row with enabled=True; the bootstrap should
    # see ``db_present=True`` and back off entirely.
    await ps.update_section(
        db_session,
        section=PlatformSettingsSection.EMAIL_SMTP,
        payload={
            "enabled": True,
            "host": "operator.set",
            "port": 25,
            "from_address": "ops@example.com",
            "use_tls": False,
        },
        actor_identity_id=identity.id,
    )
    await db_session.commit()
    await ps.bootstrap_from_env_if_empty(db_session)
    fresh = await ps.get_section(
        db_session, section=PlatformSettingsSection.EMAIL_SMTP
    )
    assert fresh.host == "operator.set"
    assert fresh.port == 25
