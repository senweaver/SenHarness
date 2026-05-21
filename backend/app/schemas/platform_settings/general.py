"""General branding + locale defaults rendered at ``/admin/settings/general``.

The IANA timezone string is validated only by length here; the
runtime callers that consume it (frontend ``Intl.DateTimeFormat`` and
backend ``zoneinfo.ZoneInfo``) raise on bad values, which is the
correct error surface — refusing the save silently would be worse
than letting the operator notice the typo at first use.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GeneralSettings(BaseModel):
    site_name: str = Field(default="SenHarness", min_length=1, max_length=120)
    site_logo_url: str | None = Field(default=None, max_length=500)
    primary_color_hex: str = Field(
        default="#3B82F6", pattern=r"^#[0-9A-Fa-f]{6}$"
    )
    default_locale: Literal["en-US", "zh-CN"] = "en-US"
    default_timezone: str = Field(default="UTC", min_length=1, max_length=64)
