"""OIDC / SAML SSO hooks (stubbed in P0, wired in P3)."""

from __future__ import annotations

from app.core.config import settings


def oidc_enabled() -> bool:
    return bool(settings.OIDC_ISSUER and settings.OIDC_CLIENT_ID and settings.OIDC_CLIENT_SECRET)
