"""Native in-process backend — pydantic-ai powered. Wires Agent + Capabilities + Toolbox + HarnessPluginHost."""

from __future__ import annotations

from app.agents.kernels.native.runner import NativeBackend
from app.agents.kernels.registry import register

register(NativeBackend())

__all__ = ["NativeBackend"]
