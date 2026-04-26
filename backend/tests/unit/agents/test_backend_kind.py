"""Tests for the relaxed ``BackendKind`` contract.

V1 widens ``BackendKind`` from a closed ``StrEnum`` to a plain constant
class so third-party adapters can register arbitrary kinds. These tests
lock in:

    * the two bundled constants still have their canonical string values
      (so existing agents don't break),
    * ``is_known_backend_kind`` returns True for bundled and False for
      anything else,
    * the constants are interchangeable with plain strings in set/dict
      operations.
"""

from __future__ import annotations

from app.db.models.agent import BackendKind, is_known_backend_kind


class TestBackendKindConstants:
    def test_native_constant(self):
        assert BackendKind.NATIVE == "native"

    def test_openclaw_constant(self):
        assert BackendKind.OPENCLAW == "openclaw"

    def test_constants_are_plain_strings(self):
        # Not a StrEnum any more — operators comparing via `==` or passing
        # to Redis / JSON should see raw strings, not enum members.
        assert isinstance(BackendKind.NATIVE, str)
        assert type(BackendKind.NATIVE) is str  # exactly str, not subclass

    def test_constants_work_in_sets(self):
        # Sanity for repositories that do `kind in {...}` style checks.
        kinds = {BackendKind.NATIVE, BackendKind.OPENCLAW}
        assert "native" in kinds
        assert "openclaw" in kinds
        assert "crewai" not in kinds


class TestIsKnownBackendKind:
    def test_known_kinds_return_true(self):
        assert is_known_backend_kind("native") is True
        assert is_known_backend_kind("openclaw") is True

    def test_unknown_kind_returns_false(self):
        # The whole point of V1's relaxation: unknown kinds are allowed
        # at the schema / DB layer, but ``is_known_backend_kind`` still
        # answers "no" so UIs can flag them with a hint.
        assert is_known_backend_kind("crewai") is False
        assert is_known_backend_kind("autogen") is False
        assert is_known_backend_kind("") is False

    def test_future_adapter_reports_as_unknown(self):
        """A community adapter registering a new kind would still be
        reported 'unknown' by this helper — that's fine; the registry
        ``get_backend`` is the real authoritative check."""
        assert is_known_backend_kind("my-runtime") is False
