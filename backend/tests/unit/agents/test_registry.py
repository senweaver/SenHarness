"""Tests for the Agent Runtime registry + its ``describe()`` output.

Ensures:
    * bundled adapters register themselves on import,
    * ``describe()`` returns stable fields the frontend relies on,
    * ``get_backend`` round-trips through ``register`` for the built-ins.
"""

from __future__ import annotations

from app.agents.kernels.base import (
    AgentBackend,
    BackendCapabilities,
    RunEvent,
    RunEventKind,
)
from app.agents.kernels.registry import (
    available_kinds,
    describe,
    get_backend,
    register,
)


class _StubBackend(AgentBackend):
    """Minimal in-memory backend used only for isolation in this file.

    We don't touch the global registry during import (which would race
    with the real adapters); instead we register + describe locally.
    """

    backend_kind = "_test_stub"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            display_name="Test Stub",
            description="For registry unit tests only.",
            docs_url="",
            supports_streaming=True,
            supports_thinking=True,
        )

    async def run(self, req):  # pragma: no cover - not exercised here
        if False:
            yield RunEvent(RunEventKind.FINAL, {})

    async def cancel(self, run_id):  # pragma: no cover
        return


class TestRegistryRoundTrip:
    def test_register_and_lookup(self):
        register(_StubBackend())
        got = get_backend("_test_stub")
        assert got is not None
        assert got.backend_kind == "_test_stub"

    def test_unknown_kind_returns_none(self):
        assert get_backend("definitely-not-registered") is None


class TestDescribeShape:
    """The frontend ``/settings/workspace/runtimes`` page and the Agent
    creation form depend on every element of the shape below — changes
    here mean a frontend change too, so they should be deliberate."""

    def test_describe_includes_stub(self):
        register(_StubBackend())
        names = {r["kind"] for r in describe()}
        assert "_test_stub" in names

    def test_each_row_has_required_fields(self):
        register(_StubBackend())
        for row in describe():
            assert set(row.keys()) >= {
                "kind",
                "display_name",
                "description",
                "docs_url",
                "requires_adapter",
                "capabilities",
            }
            caps = row["capabilities"]
            assert set(caps.keys()) >= {
                "supports_streaming",
                "supports_parallel_tools",
                "supports_thinking",
                "supports_native_mcp",
                "supports_vision",
                "max_context_tokens",
                "notes",
            }

    def test_display_name_falls_back_to_kind(self):
        class _BareBackend(_StubBackend):
            backend_kind = "_test_bare"

            def capabilities(self) -> BackendCapabilities:
                return BackendCapabilities()  # no display_name set

        register(_BareBackend())
        row = next(r for r in describe() if r["kind"] == "_test_bare")
        assert row["display_name"] == "_test_bare"

    def test_requires_adapter_flag_surfaces(self):
        """Remote backends (OpenClaw etc.) set this so the UI can render
        the backend_adapter picker only when relevant."""

        class _RemoteStub(_StubBackend):
            backend_kind = "_test_remote"

            def capabilities(self) -> BackendCapabilities:
                return BackendCapabilities(display_name="Remote", requires_adapter=True)

        register(_RemoteStub())
        row = next(r for r in describe() if r["kind"] == "_test_remote")
        assert row["requires_adapter"] is True


class TestAvailableKinds:
    def test_enumerates_registered_kinds(self):
        register(_StubBackend())
        kinds = list(available_kinds())
        assert "_test_stub" in kinds
