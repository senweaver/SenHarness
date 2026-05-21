"""Platform defaults for the M2.5.3 provider failover chain.

Workspaces opt themselves into failover via
``home_config_json["providers"]["failover_enabled"] = True``; the values
in this section seed the per-workspace defaults and supply the
platform-wide chain that workspaces inherit when they leave their own
``failover_chain`` empty.

``enabled_default = False`` is intentional: chain failover changes
provider routing semantics, so an admin must explicitly opt in before a
workspace starts swapping models mid-turn. Once opted in, a workspace
can override every knob individually — see
:mod:`app.services.provider_chain.get_workspace_failover_config`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderFailoverDefaults(BaseModel):
    """Section: ``provider_failover``."""

    enabled_default: bool = False
    cooldown_threshold_default: int = Field(ge=1, le=20, default=3)
    cooldown_seconds_default: int = Field(ge=10, le=86400, default=300)
    failover_max_attempts_default: int = Field(ge=1, le=10, default=3)
    chain_global_default: list[str] = Field(default_factory=list)
