"""M2.5.4 — MCP OAuth dance + workspace-scoped token cache.

Most enterprise MCP deployments wire OAuth 2.0 *client-credentials*
in front of the SSE / streamable-HTTP transports. This module
implements the bare minimum to unblock that flow:

* ``perform_oauth_dance`` POSTs ``client_id + client_secret + scope``
  at the IdP's token endpoint, drops the resulting bearer + refresh
  pair into the workspace vault under a deterministic ``mcp.oauth.…``
  name, and returns the freshly minted access token to the caller.
* ``get_valid_token`` reads the cached envelope and reuses the
  access_token until it lands inside the configured grace window;
  past that it triggers a refresh (or a full re-dance when no
  refresh_token was issued).

Audit emission is the caller's job — both helpers return the typed
result envelope and never write to ``access_token`` /
``refresh_token`` audit metadata, so a logged metadata dump cannot
accidentally leak the secret.

The vault entry is stored as a JSON-serialised dict (instead of a
plaintext string) so we can rotate access tokens without losing the
refresh token. This is intentional: the surrounding workspace vault
already encrypts the row at rest, so the JSON shape only matters
for in-memory consumers.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.url_safety import assert_safe_url
from app.db.models.vault import VaultItemKind
from app.services import vault as vault_svc

log = logging.getLogger(__name__)

# Default grace window — refresh the token this many seconds *before*
# the IdP-reported ``expires_at`` so a tool call can never race a
# rollover.
DEFAULT_REFRESH_GRACE_SECONDS = 300


@dataclass(slots=True)
class McpOAuthConfig:
    client_id: str
    client_secret: str  # already resolved (vault template -> plaintext)
    token_url: str
    scopes: list[str]
    refresh_grace_seconds: int = DEFAULT_REFRESH_GRACE_SECONDS

    def vault_name(self, server_id: uuid.UUID) -> str:
        """Vault item name format: ``mcp.oauth.<server_id>``.

        Per workspace + per server. Stable so subsequent dances rotate
        the same row instead of leaking ghost rows.
        """
        return f"mcp.oauth.{server_id}"


class McpOAuthError(RuntimeError):
    code = "mcp.oauth_failed"


class McpOAuthBadRequest(McpOAuthError):
    code = "mcp.oauth_bad_request"


class McpOAuthUnauthorized(McpOAuthError):
    code = "mcp.oauth_unauthorized"


@dataclass(slots=True)
class _TokenEnvelope:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds
    token_type: str = "Bearer"
    scope: str | None = None

    def as_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "token_type": self.token_type,
                "scope": self.scope,
            }
        )

    @classmethod
    def from_json(cls, blob: str) -> "_TokenEnvelope":
        payload = json.loads(blob)
        return cls(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=payload.get("refresh_token"),
            expires_at=float(payload.get("expires_at") or 0.0),
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=payload.get("scope"),
        )


@dataclass(slots=True)
class McpOAuthResult:
    access_token: str
    expires_at: float
    refreshed: bool


def _scope_param(scopes: list[str]) -> str | None:
    cleaned = [s.strip() for s in scopes if s and s.strip()]
    return " ".join(cleaned) if cleaned else None


async def _http_token_request(
    config: McpOAuthConfig,
    *,
    grant_type: str,
    extra: dict[str, str] | None = None,
) -> _TokenEnvelope:
    """POST to the IdP's token endpoint and parse the response."""
    assert_safe_url(config.token_url)
    body: dict[str, str] = {
        "grant_type": grant_type,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }
    scope = _scope_param(config.scopes)
    if scope:
        body["scope"] = scope
    if extra:
        body.update(extra)

    async with httpx.AsyncClient(timeout=30.0) as cli:
        resp = await cli.post(
            config.token_url,
            data=body,
            headers={"Accept": "application/json"},
        )
    if resp.status_code == 401:
        raise McpOAuthUnauthorized(f"401 from {config.token_url}: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise McpOAuthBadRequest(
            f"token endpoint returned {resp.status_code}: {resp.text[:200]}"
        )
    try:
        payload: dict[str, Any] = resp.json()
    except (ValueError, json.JSONDecodeError) as e:
        raise McpOAuthError(f"token endpoint returned non-JSON body: {e}") from e
    access_token = payload.get("access_token")
    if not access_token:
        raise McpOAuthError("token endpoint did not return ``access_token``")
    expires_in = int(payload.get("expires_in") or 3600)
    return _TokenEnvelope(
        access_token=str(access_token),
        refresh_token=payload.get("refresh_token"),
        expires_at=time.time() + expires_in,
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=payload.get("scope"),
    )


async def _store_envelope(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    server_id: uuid.UUID,
    config: McpOAuthConfig,
    envelope: _TokenEnvelope,
) -> None:
    name = config.vault_name(server_id)
    existing = None
    try:
        # Reveal indirectly to avoid a second round-trip — we only
        # need to know whether a row exists, the contents are
        # immaterial because we are about to overwrite them.
        await vault_svc.reveal_workspace_secret(
            db, workspace_id=workspace_id, name=name
        )
        existing = True
    except vault_svc.VaultKeyNotFoundError:
        existing = False
    if existing:
        # `reveal_workspace_secret` does not return the row; fetch it
        # via the private lookup so we can reuse `replace_secret`.
        item = await vault_svc._lookup_workspace_secret(
            db, workspace_id=workspace_id, name=name
        )
        if item is not None:
            await vault_svc.replace_secret(db, item=item, plaintext=envelope.as_json())
            return
    await vault_svc.create_secret(
        db,
        workspace_id=workspace_id,
        owner_identity_id=None,
        name=name,
        plaintext=envelope.as_json(),
        kind=VaultItemKind.OAUTH,
        metadata={
            "purpose": "mcp_oauth_token",
            "server_id": str(server_id),
            "token_url": config.token_url,
        },
    )


async def _load_envelope(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    server_id: uuid.UUID,
    config: McpOAuthConfig,
) -> _TokenEnvelope | None:
    name = config.vault_name(server_id)
    try:
        blob = await vault_svc.reveal_workspace_secret(
            db, workspace_id=workspace_id, name=name
        )
    except vault_svc.VaultKeyNotFoundError:
        return None
    try:
        return _TokenEnvelope.from_json(blob)
    except (ValueError, json.JSONDecodeError):
        log.warning("mcp oauth vault row for server=%s is corrupt; re-dancing", server_id)
        return None


async def perform_oauth_dance(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    workspace_id: uuid.UUID,
    config: McpOAuthConfig,
) -> McpOAuthResult:
    """Run a fresh client-credentials dance and persist the envelope.

    Always overwrites the vault row — callers that need a quiet
    "re-use cached when valid" shape should use
    :func:`get_valid_token` instead.
    """
    envelope = await _http_token_request(config, grant_type="client_credentials")
    await _store_envelope(
        db,
        workspace_id=workspace_id,
        server_id=server_id,
        config=config,
        envelope=envelope,
    )
    return McpOAuthResult(
        access_token=envelope.access_token,
        expires_at=envelope.expires_at,
        refreshed=False,
    )


async def get_valid_token(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    workspace_id: uuid.UUID,
    config: McpOAuthConfig,
) -> McpOAuthResult:
    """Return a non-expired token, refreshing or re-dancing if needed.

    Decision order:

    1. Cached envelope still inside the grace window → reuse.
    2. Cached envelope expired AND refresh_token present →
       ``grant_type=refresh_token`` flow; success rotates the row,
       failure falls through to (3).
    3. No cache OR refresh failed → run a fresh client-credentials
       dance.
    """
    envelope = await _load_envelope(
        db,
        workspace_id=workspace_id,
        server_id=server_id,
        config=config,
    )
    now = time.time()
    if envelope and envelope.expires_at - config.refresh_grace_seconds > now:
        return McpOAuthResult(
            access_token=envelope.access_token,
            expires_at=envelope.expires_at,
            refreshed=False,
        )

    if envelope and envelope.refresh_token:
        try:
            refreshed = await _http_token_request(
                config,
                grant_type="refresh_token",
                extra={"refresh_token": envelope.refresh_token},
            )
        except McpOAuthError as e:
            log.warning(
                "mcp oauth refresh failed server=%s err=%s — re-dancing",
                server_id,
                e,
            )
        else:
            await _store_envelope(
                db,
                workspace_id=workspace_id,
                server_id=server_id,
                config=config,
                envelope=refreshed,
            )
            return McpOAuthResult(
                access_token=refreshed.access_token,
                expires_at=refreshed.expires_at,
                refreshed=True,
            )

    fresh = await perform_oauth_dance(
        db,
        server_id=server_id,
        workspace_id=workspace_id,
        config=config,
    )
    return McpOAuthResult(
        access_token=fresh.access_token,
        expires_at=fresh.expires_at,
        refreshed=bool(envelope),
    )


__all__ = [
    "DEFAULT_REFRESH_GRACE_SECONDS",
    "McpOAuthBadRequest",
    "McpOAuthConfig",
    "McpOAuthError",
    "McpOAuthResult",
    "McpOAuthUnauthorized",
    "get_valid_token",
    "perform_oauth_dance",
]
