"""Sandbox safety toggles — every field is a dangerous-change candidate.

Flipping ``allow_local_execute_in_prod = True`` removes the
``SANDBOX_LOCAL_EXECUTE_PROD`` guard rail in
:mod:`app.core.config`, letting agents run shell commands directly
inside the backend process. ``allow_ssh_backend`` likewise opens the
``backend.kind = ssh`` adapter for production. Both are gated by the
M0.13 ``confirmed_dangerous`` flag and emit a separate
``platform_settings.dangerous_change`` audit row.
"""

from __future__ import annotations

from pydantic import BaseModel


class SecuritySandboxSettings(BaseModel):
    allow_local_execute_in_prod: bool = False
    allow_ssh_backend: bool = False
    require_command_allowlist_in_prod: bool = True
