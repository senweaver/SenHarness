"""Unit tests for the sandbox policy normalizer and security guards.

These tests intentionally don't spin up real Docker / LocalBackend — they
focus on the defaulting logic and the ``SandboxMisconfiguredError`` production
guard, which is the security-critical surface we hardened in V1.
"""

from __future__ import annotations

import pytest

from app.agents.harness import sandbox
from app.agents.harness.sandbox import (
    SandboxMisconfiguredError,
    _normalize,
    build_sandbox,
)


# ─── Policy normalization ────────────────────────────────────
class TestNormalize:
    def test_none_policy_yields_none(self):
        assert _normalize(None) is None

    def test_missing_sandbox_key_yields_none(self):
        assert _normalize({"other": 1}) is None

    def test_false_disables_sandbox(self):
        assert _normalize({"sandbox": False}) is None

    def test_true_shortcut_maps_to_local(self):
        assert _normalize({"sandbox": True}) == {"kind": "local"}

    def test_string_maps_to_kind(self):
        assert _normalize({"sandbox": "docker"}) == {"kind": "docker"}
        assert _normalize({"sandbox": "STATE"}) == {"kind": "state"}  # normalized lower

    def test_dict_preserves_keys(self):
        spec = _normalize({"sandbox": {"kind": "docker", "image": "alpine"}})
        assert spec is not None
        assert spec["kind"] == "docker"
        assert spec["image"] == "alpine"

    def test_dict_without_kind_defaults_to_local(self):
        spec = _normalize({"sandbox": {"execute": True}})
        assert spec is not None
        assert spec["kind"] == "local"

    def test_invalid_spec_type_yields_none(self):
        assert _normalize({"sandbox": 42}) is None
        assert _normalize({"sandbox": ["list"]}) is None


# ─── Production guard ────────────────────────────────────────
class TestProductionGuard:
    """``local`` sandbox with shell execution is blocked in prod by default.

    This is the critical safety default V1 introduces: without this guard a
    misconfigured agent would run arbitrary shell on the SenHarness backend
    host (not on the agent's own sandbox container).
    """

    def test_local_execute_true_in_prod_is_blocked(self, monkeypatch):
        monkeypatch.setattr(sandbox.settings, "APP_ENV", "production")
        monkeypatch.setattr(sandbox.settings, "SANDBOX_LOCAL_EXECUTE_PROD", False)

        with pytest.raises(SandboxMisconfiguredError) as exc_info:
            build_sandbox(policy={"sandbox": {"kind": "local", "execute": True}})

        assert exc_info.value.code == "sandbox.local_execute_blocked_in_prod"

    def test_local_execute_false_in_prod_is_ok(self, monkeypatch):
        """The guard only fires when ``execute=True`` — filesystem-only local
        sandbox remains available in prod."""
        monkeypatch.setattr(sandbox.settings, "APP_ENV", "production")

        # No exception: exec disabled means no shell, which is safe.
        try:
            build_sandbox(policy={"sandbox": {"kind": "local", "execute": False}})
        except SandboxMisconfiguredError:
            pytest.fail("execute=False must not trigger the prod guard")
        except Exception:
            # ConsoleCapability / LocalBackend may fail to construct without
            # the optional ``pydantic-ai-backends`` install — that's a
            # separate integration concern and irrelevant to the guard test.
            pass

    def test_opt_in_override_bypasses_guard(self, monkeypatch):
        monkeypatch.setattr(sandbox.settings, "APP_ENV", "production")
        monkeypatch.setattr(sandbox.settings, "SANDBOX_LOCAL_EXECUTE_PROD", True)

        try:
            build_sandbox(policy={"sandbox": {"kind": "local", "execute": True}})
        except SandboxMisconfiguredError:
            pytest.fail("override flag must bypass the prod guard")
        except Exception:
            pass  # unrelated capability-init failures are ok here

    def test_docker_kind_in_prod_with_execute_is_allowed(self, monkeypatch):
        """``docker`` kind runs in a separate container, so shell there is
        not equivalent to compromising the SenHarness backend — the guard
        applies only to ``local``."""
        monkeypatch.setattr(sandbox.settings, "APP_ENV", "production")
        monkeypatch.setattr(sandbox.settings, "SANDBOX_LOCAL_EXECUTE_PROD", False)

        try:
            build_sandbox(
                policy={"sandbox": {"kind": "docker", "execute": True}}
            )
        except SandboxMisconfiguredError:
            pytest.fail("docker kind must not trigger the local-exec guard")
        except Exception:
            # Missing Docker daemon / SDK at test time is unrelated.
            pass

    def test_development_permits_local_execute(self, monkeypatch):
        monkeypatch.setattr(sandbox.settings, "APP_ENV", "development")

        try:
            build_sandbox(policy={"sandbox": {"kind": "local", "execute": True}})
        except SandboxMisconfiguredError:
            pytest.fail("development env must not trigger the prod guard")
        except Exception:
            pass


# ─── Ruleset defaulting ──────────────────────────────────────
class TestRulesetDefault:
    """We shipped with ``PERMISSIVE`` before V1. V1 ratchets the default to
    ``DEFAULT`` (conservative) to prevent accidental dangerous command
    allowlists when operators forget to set ``permissions``."""

    def test_resolve_unknown_name_falls_back_to_default(self):
        # ``_resolve_ruleset`` requires pydantic-ai-backends at import time;
        # if it's missing, the helper returns None and the sandbox is skipped
        # further up. Only assert when the backends package is present.
        pytest.importorskip("pydantic_ai_backends")
        from pydantic_ai_backends.permissions import DEFAULT_RULESET

        assert sandbox._resolve_ruleset("nonexistent") is DEFAULT_RULESET
        assert sandbox._resolve_ruleset(None) is DEFAULT_RULESET

    def test_resolve_known_names(self):
        pytest.importorskip("pydantic_ai_backends")
        from pydantic_ai_backends.permissions import (
            DEFAULT_RULESET,
            PERMISSIVE_RULESET,
            READONLY_RULESET,
            STRICT_RULESET,
        )

        assert sandbox._resolve_ruleset("default") is DEFAULT_RULESET
        assert sandbox._resolve_ruleset("permissive") is PERMISSIVE_RULESET
        assert sandbox._resolve_ruleset("readonly") is READONLY_RULESET
        assert sandbox._resolve_ruleset("strict") is STRICT_RULESET
        assert sandbox._resolve_ruleset("DEFAULT") is DEFAULT_RULESET  # case-insensitive


# ─── Disabled path ───────────────────────────────────────────
class TestDisabledSandbox:
    def test_no_policy_returns_none_none(self):
        assert build_sandbox(policy=None) == (None, None)

    def test_empty_policy_returns_none_none(self):
        assert build_sandbox(policy={}) == (None, None)

    def test_sandbox_false_returns_none_none(self):
        assert build_sandbox(policy={"sandbox": False}) == (None, None)

