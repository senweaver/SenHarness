"""Capability descriptor for the native backend."""

from __future__ import annotations

from app.agents.kernels.base import BackendCapabilities

CAPABILITIES = BackendCapabilities(
    supports_streaming=True,
    supports_parallel_tools=True,
    supports_thinking=True,
    supports_native_mcp=True,
    supports_vision=True,
    max_context_tokens=None,
    notes="Native in-process backend with harness middleware plugin host.",
    display_name="NativeRuntime",
    description=(
        "In-process runtime. Fastest path — no network hop. Recommended "
        "default for new agents."
    ),
    docs_url="",
    requires_adapter=False,
)
