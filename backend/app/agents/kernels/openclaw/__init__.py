"""OpenClaw remote backend — second citizen in ``agents.kernels.registry``.

Registering on import means ``get_backend("openclaw")`` resolves after
``app.main`` imports this module at startup. Kept tiny so the module graph
stays cheap when a workspace has no openclaw adapters at all.
"""

from __future__ import annotations

from app.agents.kernels.openclaw.adapter import OpenClawBackend
from app.agents.kernels.registry import register

register(OpenClawBackend())

__all__ = ["OpenClawBackend"]
