"""Cross-platform session routing platform / workspace settings (M3.6).

Backs the ``session.routing`` admin section. Same shape powers both the
platform-wide default row (``system_settings.session_routing_defaults``)
and the per-workspace override at
``workspace.home_config_json["session_routing"]``; the workspace block
wins on a field-by-field basis.

Defaults are deliberately conservative:

* ``cross_platform_enabled = False`` — opt-in. Until a workspace admin
  flips this, every inbound IM message lands on a per-channel session
  the same way it always has, and no logical thread row is ever
  created. The behaviour change for existing workspaces is exactly
  zero.
* ``pairing_required_for_cross_platform = True`` — when continuity is
  enabled, the dispatcher still refuses to merge sessions across
  channels until the user has completed the explicit 6-digit pair on
  both sides. This stops a single shared identity (e.g. "anonymous"
  on a webhook channel) from collapsing two unrelated threads.
* ``pairing_code_ttl_seconds = 600`` — ten minutes. Short enough to
  stop a leaked code from being burned an hour later, long enough for
  a cross-platform handshake that involves switching apps.
* ``default_strategy = "per_channel"`` — preserves the legacy routing
  contract for any caller that asks for the strategy directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SessionRoutingDefaults(BaseModel):
    default_strategy: Literal["per_channel", "logical_thread"] = "per_channel"
    cross_platform_enabled: bool = False
    pairing_required_for_cross_platform: bool = True
    pairing_code_ttl_seconds: int = Field(ge=60, le=86400, default=600)
