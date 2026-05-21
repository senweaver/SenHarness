"""Defaults applied to every freshly-created workspace.

Existing workspaces are NOT mutated when an admin saves this
section — workspaces own their own ``branding_json`` /
``home_config_json`` and are sticky. The defaults only affect the
next ``POST /workspaces`` call (and the M0.9 personal-workspace
auto-provisioner).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WorkspaceDefaultsSettings(BaseModel):
    branding_agent_term_default: Literal[
        "agent", "default", "digital_employee", "partner", "secretary"
    ] = "agent"
    new_workspace_default_model: str | None = Field(default=None, max_length=120)
    new_workspace_sandbox_kind: Literal["docker", "ssh", "local"] = "docker"
