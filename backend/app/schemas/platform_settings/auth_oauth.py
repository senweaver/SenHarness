"""OAuth provider catalog rendered at ``/admin/settings/auth.oauth``.

``client_secret_ref`` is a vault key name, never the raw secret. The
admin UI shows a "Set / rotate secret" button that POSTs the raw
secret straight to a separate vault endpoint and stores only the
returned reference in this section. ``EmailSmtpSettings.password_ref``
follows the same pattern.

M0.13 ships the form + an OAuth metadata-validation test button only;
the live consent dance lands with M3 SSO.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OAuthProvider(BaseModel):
    name: Literal["github", "google", "microsoft", "feishu"]
    enabled: bool = False
    client_id: str | None = Field(default=None, max_length=255)
    client_secret_ref: str | None = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=list)


class AuthOAuthSettings(BaseModel):
    providers: list[OAuthProvider] = Field(default_factory=list)
    auto_link_existing_email: bool = True
