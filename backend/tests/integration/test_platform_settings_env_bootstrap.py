"""Env bootstrap path: empty DB → seeded from env vars."""

from __future__ import annotations

import pytest

from app.services import platform_settings as ps

pytestmark = pytest.mark.asyncio


async def test_bootstrap_seeds_smtp_from_env(db_session, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_FROM", "ops@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    ps.invalidate_local()
    # Wipe any pre-existing row (e.g. from a previous test in the session).
    from app.services.system_settings import (
        SystemSettingKey,
        delete_system_setting,
    )

    await delete_system_setting(db_session, SystemSettingKey.EMAIL_SMTP)
    await db_session.commit()

    seeded = await ps.bootstrap_from_env_if_empty(db_session)
    assert PSEC_EMAIL_SMTP in seeded

    fresh = await ps.get_section(db_session, section=ps.PlatformSettingsSection.EMAIL_SMTP)
    assert fresh.host == "smtp.example.com"
    assert fresh.port == 2525
    assert fresh.use_tls is False


async def test_bootstrap_seeds_oauth_from_env(db_session, monkeypatch):
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc123")
    ps.invalidate_local()
    from app.services.system_settings import (
        SystemSettingKey,
        delete_system_setting,
    )

    await delete_system_setting(db_session, SystemSettingKey.AUTH_OAUTH)
    await db_session.commit()

    await ps.bootstrap_from_env_if_empty(db_session)
    fresh = await ps.get_section(db_session, section=ps.PlatformSettingsSection.AUTH_OAUTH)
    providers = {p.name: p for p in fresh.providers}
    assert "github" in providers
    assert providers["github"].client_id == "abc123"


async def test_bootstrap_idempotent(db_session, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "ops@example.com")
    ps.invalidate_local()
    from app.services.system_settings import (
        SystemSettingKey,
        delete_system_setting,
    )

    await delete_system_setting(db_session, SystemSettingKey.EMAIL_SMTP)
    await db_session.commit()
    first = await ps.bootstrap_from_env_if_empty(db_session)
    assert first.get(PSEC_EMAIL_SMTP)

    second = await ps.bootstrap_from_env_if_empty(db_session)
    # The row already exists; bootstrap should silently skip.
    assert PSEC_EMAIL_SMTP not in second


PSEC_EMAIL_SMTP = ps.PlatformSettingsSection.EMAIL_SMTP.value
