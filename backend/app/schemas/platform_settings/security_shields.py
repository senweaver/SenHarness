"""Platform-level shield defaults applied to new agents.

The level enums mirror the ``pydantic-ai-shields`` taxonomy that the
runner uses today — keeping them as ``Literal`` strings rather than
StrEnum lets the schema-driven form render them as a select without
exporting an enum class through the wire DTO.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SecurityShieldsSettings(BaseModel):
    pii_default_level: Literal["off", "log", "block"] = "log"
    secret_redaction_default_enabled: bool = True
    prompt_injection_default_level: Literal["low", "medium", "high"] = "medium"
    new_agent_default_enabled: bool = True
