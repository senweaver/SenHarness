"""OAuth provider registration (Authlib).

Registers Google / GitHub / Microsoft when their env-level client id + secret
are present. Frontend flow:

    1. User clicks *Sign in with X* → `GET /auth/oauth/{provider}/start` →
       302 to the IdP authorize URL. CSRF state lives in the server session.
    2. IdP redirects to `GET /auth/oauth/{provider}/callback?code=...&state=...`.
       We exchange for a token, resolve profile → provision-or-lookup an
       `Identity`, issue the same cookies as a normal login, then 302 the
       browser back to ``/`` (or a frontend-supplied ``next`` param).

Identities are matched primarily by ``(oauth_provider, oauth_id)``; as a
fallback we match by email to allow linking existing password accounts to
their SSO identity. The email match is opt-in via the normal login flow —
the OAuth path always takes the linked identity when one exists.
"""

from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth

from app.core.config import settings

log = logging.getLogger(__name__)

oauth = OAuth()

# ─── Google (OpenID Connect) ──────────────────────────────
if settings.OAUTH_GOOGLE_CLIENT_ID and settings.OAUTH_GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=settings.OAUTH_GOOGLE_CLIENT_ID,
        client_secret=settings.OAUTH_GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# ─── GitHub (OAuth2) ──────────────────────────────────────
if settings.OAUTH_GITHUB_CLIENT_ID and settings.OAUTH_GITHUB_CLIENT_SECRET:
    oauth.register(
        name="github",
        client_id=settings.OAUTH_GITHUB_CLIENT_ID,
        client_secret=settings.OAUTH_GITHUB_CLIENT_SECRET,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user user:email"},
    )

# ─── Microsoft (v2 common tenant) ─────────────────────────
if settings.OAUTH_MICROSOFT_CLIENT_ID and settings.OAUTH_MICROSOFT_CLIENT_SECRET:
    oauth.register(
        name="microsoft",
        client_id=settings.OAUTH_MICROSOFT_CLIENT_ID,
        client_secret=settings.OAUTH_MICROSOFT_CLIENT_SECRET,
        server_metadata_url=(
            "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile User.Read"},
    )


def registered_providers() -> list[str]:
    """List of provider names currently wired in env."""
    return [
        name
        for name in ("google", "github", "microsoft")
        if getattr(oauth, name, None) is not None
    ]
