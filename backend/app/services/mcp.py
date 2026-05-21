"""MCP service helpers.

Two roles in this module:

* The original M0 helpers (``get_server_or_404`` / ``ping_server``)
  which back the catalogue REST endpoints. Kept ``as is`` so workspace
  admins can still register MCP servers and sanity-check connectivity
  before any agent run touches them.
* The M2.5.4 helpers (``build_mcp_client_for_server`` /
  ``list_tools_uniform``) which translate an ``McpServer`` row plus
  workspace context into a connected :class:`McpClient` — including
  OAuth resolution, vault template substitution, and audit hooks.

Both halves stay in this single module so existing callers keep
working unchanged; the new code lives in a clearly delimited
``M2.5.4`` block at the bottom.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.mcp import McpServer, McpServerHealth, ToolBinding, Toolbox
from app.db.session import get_session_factory
from app.repositories.mcp import McpServerRepository, ToolBindingRepository, ToolboxRepository
from app.services import audit as audit_svc
from app.services import vault as vault_svc
from app.services.mcp_client import (
    McpClient,
    McpClientConfig,
    McpClientError,
    McpConfigInvalid,
    McpToolDescriptor,
    McpTransport,
)
from app.services.mcp_oauth import (
    McpOAuthConfig,
    McpOAuthError,
    get_valid_token,
)

log = logging.getLogger(__name__)


async def get_server_or_404(
    session: AsyncSession, *, server_id: uuid.UUID, workspace_id: uuid.UUID
) -> McpServer:
    row = await McpServerRepository(session).get(server_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("mcp_server_not_found", code="mcp.server_not_found")
    return row


async def get_toolbox_or_404(
    session: AsyncSession, *, toolbox_id: uuid.UUID, workspace_id: uuid.UUID
) -> Toolbox:
    row = await ToolboxRepository(session).get(toolbox_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("toolbox_not_found", code="mcp.toolbox_not_found")
    return row


async def get_binding_or_404(
    session: AsyncSession, *, binding_id: uuid.UUID, workspace_id: uuid.UUID
) -> ToolBinding:
    row = await ToolBindingRepository(session).get(binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("tool_binding_not_found", code="mcp.tool_binding_not_found")
    return row


async def ping_server(server: McpServer) -> tuple[McpServerHealth, str]:
    """Best-effort health check for an MCP server entry."""
    if not server.enabled:
        return McpServerHealth.DOWN, "server disabled"
    transport = (server.transport or "").lower()
    if transport in {"stdio", "command"}:
        # Local process transport can only be verified at runtime by actually
        # spawning the MCP process; here we can only validate config shape.
        if not server.command:
            return McpServerHealth.DOWN, "missing command"
        return McpServerHealth.UNKNOWN, "command configured"
    endpoint = (server.url or server.endpoint or "").strip()
    if not endpoint:
        return McpServerHealth.DOWN, "missing endpoint"

    health_url = endpoint
    if not endpoint.endswith("/health"):
        health_url = urljoin(endpoint.rstrip("/") + "/", "health")
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            resp = await cli.get(health_url)
        if resp.status_code < 300:
            return McpServerHealth.HEALTHY, f"{resp.status_code} {health_url}"
        if resp.status_code < 500:
            return McpServerHealth.DEGRADED, f"{resp.status_code} {health_url}"
        return McpServerHealth.DOWN, f"{resp.status_code} {health_url}"
    except httpx.HTTPError as e:
        return McpServerHealth.DOWN, str(e)


# ─── M2.5.4 client glue ─────────────────────────────────────────
def _normalise_transport(raw: str | None) -> McpTransport:
    """Map the legacy ``http`` / ``command`` aliases to canonical values."""
    value = (raw or "stdio").strip().lower()
    if value in ("stdio", "command"):
        return McpTransport.STDIO
    if value == "sse":
        return McpTransport.SSE
    if value in ("streamable_http", "http", "https", "streamable-http"):
        return McpTransport.STREAMABLE_HTTP
    raise McpConfigInvalid(f"unknown transport {raw!r}")


def _split_command(server: McpServer) -> list[str]:
    """Stitch ``command`` + ``args_json`` into a single argv list."""
    base = (server.command or "").strip()
    if not base:
        return []
    parts: list[str] = base.split()
    args = server.args_json or []
    if isinstance(args, list):
        parts.extend(str(a) for a in args)
    return [p for p in parts if p]


def _build_oauth_config_from_auth(
    auth_json: dict[str, Any] | None, *, client_secret: str
) -> McpOAuthConfig:
    auth = auth_json or {}
    return McpOAuthConfig(
        client_id=str(auth.get("client_id") or ""),
        client_secret=client_secret,
        token_url=str(auth.get("token_url") or ""),
        scopes=list(auth.get("scopes") or []),
        refresh_grace_seconds=int(auth.get("refresh_grace_seconds") or 300),
    )


async def _resolve_oauth_secret(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth_json: dict[str, Any],
) -> str:
    """Resolve the OAuth client secret from the configured source.

    Two formats are supported:

    * ``client_secret_ref`` — a ``vault://workspace/<name>`` template
      that we resolve against the calling workspace's vault.
    * ``client_secret`` — inline plaintext. The schema layer
      validates this only at *create / update* time; the client never
      writes it back into ``auth_json``, so the field disappears
      after the next save unless the operator deliberately keeps it.

    The ref form is the long-term shape — inline secrets exist only
    so the platform admin can paste a value once during the initial
    save and have it migrated into the vault on the same request.
    """
    ref = auth_json.get("client_secret_ref")
    if isinstance(ref, str) and ref.strip():
        return await vault_svc.resolve_vault_template(
            db, workspace_id=workspace_id, template=ref
        )
    inline = auth_json.get("client_secret")
    if isinstance(inline, str) and inline.strip():
        return inline.strip()
    raise McpOAuthError(
        "auth_json.oauth requires either ``client_secret_ref`` "
        "(vault template) or ``client_secret`` (inline)"
    )


async def _build_audit_hook(
    *,
    workspace_id: uuid.UUID,
    server_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Return an async callback the client can use to record audit rows.

    The hook opens a fresh DB session per audit row — long-running
    keepalive loops would otherwise pin the request session for the
    lifetime of the connection. ``access_token`` / ``refresh_token``
    are NEVER passed through this hook (they're stripped by the
    client before the audit fires).
    """
    factory = get_session_factory()

    async def _hook(action: str, payload: dict[str, Any]) -> None:
        # Strip any token-shaped keys before we persist — defence in depth.
        scrubbed = {
            k: v
            for k, v in payload.items()
            if k not in {"access_token", "refresh_token", "client_secret"}
        }
        try:
            async with factory() as db:
                await audit_svc.record(
                    db,
                    action=action,
                    actor_identity_id=actor_identity_id,
                    workspace_id=workspace_id,
                    resource_type="mcp_server",
                    resource_id=server_id,
                    summary=action,
                    metadata=scrubbed,
                )
                await db.commit()
        except Exception:  # pragma: no cover — defensive
            log.warning("mcp audit hook failed action=%s", action, exc_info=True)

    return _hook


async def build_mcp_client_for_server(
    db: AsyncSession,
    *,
    server: McpServer,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None = None,
) -> McpClient:
    """Translate a row + workspace context into a connected client.

    Steps:

    1. Pick the transport (``stdio`` / ``sse`` / ``streamable_http``)
       from the dedicated column, defaulting to ``stdio`` when the
       row predates M2.5.4.
    2. Resolve auth — either inline bearer (``auth_json.bearer``),
       templated vault refs in ``env_json`` for stdio, or a fresh
       OAuth dance for ``auth_json.type='oauth'``.
    3. Hand the resulting :class:`McpClientConfig` to
       :meth:`McpClient.connect`, which finishes the SDK handshake
       and starts the keepalive loop.

    The caller owns the returned client and **must** ``await
    client.close()`` when done (or use the client as an async context
    manager). The function itself returns synchronously — the SDK's
    own background streams stay attached to the calling task group.
    """
    if not server.enabled:
        raise McpClientError("server is disabled")
    transport = _normalise_transport(server.transport)
    auth_json: dict[str, Any] = dict(server.auth_json or {})
    bearer_token: str | None = None

    if auth_json.get("type") == "oauth":
        if transport == McpTransport.STDIO:
            raise McpConfigInvalid(
                "OAuth is only meaningful for sse / streamable_http transports"
            )
        client_secret = await _resolve_oauth_secret(
            db, workspace_id=workspace_id, auth_json=auth_json
        )
        oauth_config = _build_oauth_config_from_auth(auth_json, client_secret=client_secret)
        oauth_result = await get_valid_token(
            db,
            server_id=server.id,
            workspace_id=workspace_id,
            config=oauth_config,
        )
        bearer_token = oauth_result.access_token
        await audit_svc.record(
            db,
            action=(
                "mcp.oauth_token_refreshed"
                if oauth_result.refreshed
                else "mcp.oauth_token_acquired"
            ),
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="mcp_server",
            resource_id=server.id,
            summary=f"resolved oauth token for {server.slug}",
            metadata={
                "server_slug": server.slug,
                "expires_at": int(oauth_result.expires_at),
                "refreshed": oauth_result.refreshed,
            },
        )
    elif isinstance(auth_json.get("bearer"), str):
        bearer_token = auth_json["bearer"].strip() or None
    elif isinstance(auth_json.get("bearer_ref"), str):
        bearer_token = (
            await vault_svc.resolve_vault_template(
                db, workspace_id=workspace_id, template=auth_json["bearer_ref"]
            )
        ).strip() or None

    headers: dict[str, str] = {}
    raw_headers = auth_json.get("headers")
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            if not isinstance(value, str):
                continue
            resolved = await vault_svc.resolve_vault_template(
                db, workspace_id=workspace_id, template=value
            )
            headers[str(key)] = resolved

    config = McpClientConfig(
        transport=transport,
        command=_split_command(server) if transport == McpTransport.STDIO else None,
        env=server.env_json or None,
        url=(server.url or server.endpoint or None) if transport != McpTransport.STDIO else None,
        headers=headers or None,
        bearer_token=bearer_token,
        keepalive_seconds=int(
            (server.metadata_json or {}).get("keepalive_seconds")
            or auth_json.get("keepalive_seconds")
            or 30
        ),
        request_timeout_seconds=int(
            (server.metadata_json or {}).get("request_timeout_seconds")
            or auth_json.get("request_timeout_seconds")
            or 300
        ),
        max_concurrent=int(
            (server.metadata_json or {}).get("max_concurrent")
            or auth_json.get("max_concurrent")
            or 4
        ),
        server_slug=server.slug,
    )

    audit_hook = await _build_audit_hook(
        workspace_id=workspace_id,
        server_id=server.id,
        actor_identity_id=actor_identity_id,
    )

    try:
        client = await McpClient.connect(config, on_audit=audit_hook)
    except McpOAuthError as e:
        await audit_svc.record(
            db,
            action="mcp.oauth_failed",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="mcp_server",
            resource_id=server.id,
            summary=f"oauth failed for {server.slug}",
            metadata={"server_slug": server.slug, "code": getattr(e, "code", "mcp.oauth_failed")},
        )
        raise
    return client


async def list_tools_uniform(
    db: AsyncSession,
    *,
    server: McpServer,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None = None,
) -> list[McpToolDescriptor]:
    """Backward-compatible tool catalogue probe.

    Connects, lists, and tears down the client in one shot. Used by
    the catalogue REST endpoint to populate the workspace UI without
    forcing the operator to start a real chat turn first.
    """
    client = await build_mcp_client_for_server(
        db,
        server=server,
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
    )
    try:
        return await client.list_tools()
    finally:
        await client.close()


__all__ = [
    "build_mcp_client_for_server",
    "get_binding_or_404",
    "get_server_or_404",
    "get_toolbox_or_404",
    "list_tools_uniform",
    "ping_server",
]
