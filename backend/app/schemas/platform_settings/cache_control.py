"""Platform defaults for the M2.5.9 provider-side cache marker.

The runner reads :class:`CacheControlDefaults` to decide whether to
annotate outbound payloads with provider cache markers, what TTL to
request, and how aggressively the adaptive disable window should
trip when a provider stops honouring the markers.

Workspace overrides live under
``workspace.home_config_json["providers"]["cache_control"]``; the
platform defaults here back-fill any unspecified knob and give a
fresh deployment a safe-on posture.

``ttl_default = "5m"`` (Anthropic ephemeral) is the conservative
choice — the 1-hour beta is gated behind the ``extended-cache-ttl-
2025-04-11`` Anthropic header and operators must opt their workspace
in explicitly. The adaptive disable parameters mirror the runtime
constants in :mod:`app.services.cache_adaptive` so the admin section
schema and the live tracker share one source of truth.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CacheControlDefaults(BaseModel):
    """Section: ``cache_control``."""

    enabled_default: bool = True
    min_prompt_tokens_default: int = Field(ge=128, le=10_000, default=1024)
    max_breakpoints_default: int = Field(ge=1, le=8, default=4)
    ttl_default: Literal["5m", "1h"] = "5m"
    adaptive_disable_threshold: int = Field(ge=1, le=20, default=5)
    adaptive_disable_duration_seconds: int = Field(
        ge=10, le=600, default=60
    )
