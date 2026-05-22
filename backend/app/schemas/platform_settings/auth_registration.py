"""Aggregated registration settings — replaces three separate keys.

M0.9 stored ``REGISTRATION_MODE`` and
``AUTH_REQUIRE_EMAIL_VERIFICATION`` as flat KV rows; M0.13 surfaces
both in one section together with the env-driven
``AUTH_REGISTER_RATE_LIMIT`` for the admin form. The legacy keys keep
working — :func:`app.services.platform_settings.update_section`
splits the merged payload back into the original
``SystemSettingKey`` rows so existing readers (e.g.
``app.services.auth.get_registration_mode``) need no change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuthRegistrationSettings(BaseModel):
    mode: Literal["open_personal", "open_invite_only", "closed"] = "open_personal"
    require_email_verification: bool = False
    rate_limit_per_minute: int = Field(default=3, ge=1, le=200)
    invitation_expiry_days: int = Field(default=30, ge=1, le=365)
