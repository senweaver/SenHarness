"""Registry of Agent Runtime backends. New engines register here to be selectable."""

from __future__ import annotations

from collections.abc import Iterable

from app.agents.kernels.base import AgentBackend

_registry: dict[str, AgentBackend] = {}


def register(backend: AgentBackend) -> AgentBackend:
    _registry[backend.backend_kind] = backend
    return backend


def get_backend(kind: str) -> AgentBackend | None:
    return _registry.get(kind)


def available_kinds() -> Iterable[str]:
    return _registry.keys()


def describe() -> list[dict]:
    """Return a JSON-ready summary of every registered runtime.

    Drives the ``GET /api/v1/agents/runtimes`` endpoint and the admin-UI
    runtime picker. Fields come straight from ``BackendCapabilities`` —
    adapters that fill in ``display_name`` / ``description`` / ``docs_url``
    get pretty UI cards for free; bare-bones adapters still appear but
    fall back to the ``kind`` string.
    """
    out: list[dict] = []
    for kind, backend in _registry.items():
        caps = backend.capabilities()
        out.append(
            {
                "kind": kind,
                "display_name": caps.display_name or kind,
                "description": caps.description,
                "docs_url": caps.docs_url,
                "requires_adapter": caps.requires_adapter,
                "capabilities": {
                    "supports_streaming": caps.supports_streaming,
                    "supports_parallel_tools": caps.supports_parallel_tools,
                    "supports_thinking": caps.supports_thinking,
                    "supports_native_mcp": caps.supports_native_mcp,
                    "supports_vision": caps.supports_vision,
                    "max_context_tokens": caps.max_context_tokens,
                    "notes": caps.notes,
                },
            }
        )
    return out
