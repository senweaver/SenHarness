"""Pure-function tests for the PENDING identity gate (M0.9)."""

from __future__ import annotations

from app.core.email_verification_gate import is_pending_safe_route


def test_whitelist_routes_are_safe():
    assert is_pending_safe_route("/api/v1/me") is True
    assert is_pending_safe_route("/api/v1/auth/logout") is True
    assert is_pending_safe_route("/api/v1/auth/resend-verification") is True
    assert is_pending_safe_route("/api/v1/auth/registration-mode") is True
    assert is_pending_safe_route("/api/v1/auth/verify-email/abc123") is True
    assert is_pending_safe_route("/api/v1/auth/refresh") is True
    assert is_pending_safe_route("/api/v1/auth/oauth/google/start") is True


def test_business_routes_are_blocked():
    assert is_pending_safe_route("/api/v1/agents") is False
    assert is_pending_safe_route("/api/v1/agents/abc/run") is False
    assert is_pending_safe_route("/api/v1/sessions") is False
    assert is_pending_safe_route("/api/v1/workspaces") is False
    assert is_pending_safe_route("/api/v1/skills") is False


def test_non_api_paths_pass_through():
    assert is_pending_safe_route("/healthz") is True
    assert is_pending_safe_route("/admin/sql/") is True
    assert is_pending_safe_route("/docs") is True
