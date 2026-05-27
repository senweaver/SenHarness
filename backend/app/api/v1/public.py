"""Unauthenticated bootstrap endpoint.

Exposes the few platform-level values the frontend needs *before* the
user has signed in (default locale for the login page, branding hex,
registration mode for the "create an account" CTA). Everything else
stays authenticated.

Rate-limited because it touches the platform-settings reader on every
hit; a runaway frontend retry loop must not hammer the DB.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import DBSession
from app.core.rate_limit import rate_limit
from app.services import platform_settings as ps_svc
from app.services.platform_settings import PlatformSettingsSection
from app.services.system_settings import SystemSettingKey, get_system_setting

router = APIRouter()


class BootstrapOut(BaseModel):
    """Public branding + locale defaults rendered on every cold start."""

    site_name: str
    primary_color_hex: str
    default_locale: str
    default_timezone: str
    registration_mode: str


@router.get(
    "/bootstrap",
    response_model=BootstrapOut,
    dependencies=[Depends(rate_limit("public_bootstrap", limit=120, period_seconds=60))],
)
async def read_bootstrap(db: DBSession) -> BootstrapOut:
    """Return the tiny payload the unauthenticated UI needs to render."""
    general = await ps_svc.get_section(db, section=PlatformSettingsSection.GENERAL)
    mode = await get_system_setting(
        db, SystemSettingKey.REGISTRATION_MODE, default="open_personal"
    )
    return BootstrapOut(
        site_name=getattr(general, "site_name", "SenHarness"),
        primary_color_hex=getattr(general, "primary_color_hex", "#3B82F6"),
        default_locale=getattr(general, "default_locale", "en-US"),
        default_timezone=getattr(general, "default_timezone", "UTC"),
        registration_mode=str(mode or "open_personal"),
    )
