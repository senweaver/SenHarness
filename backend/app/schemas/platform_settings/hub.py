"""Skill Hub platform settings (M3.1).

Drives the M3 federation surface. ``default_scope`` controls which
scope a workspace-level promote verb files when the caller doesn't
specify one — the M3.3 implementation will respect this knob; M3.1
stores it for forward compatibility. ``require_admin_for_platform_promote``
is informational at M3.1 (the API gate already requires platform
admin) but lets a future SaaS deployment relax the rule for trusted
tenants without code changes. ``sanitizer_required`` will be
consulted by the M3.2 sanitizer; M3.1 keeps it default-on so a
workspace cannot accidentally pull a hub pack while sanitization is
still being wired.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HubSettings(BaseModel):
    enabled: bool = True
    default_scope: Literal["platform", "tenant"] = "tenant"
    require_admin_for_platform_promote: bool = True
    auto_pull_enabled_default: bool = False
    sanitizer_required: bool = True
