"""MCP service helpers."""

from __future__ import annotations

import uuid
from urllib.parse import urljoin

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.mcp import McpServer, McpServerHealth, ToolBinding, Toolbox
from app.repositories.mcp import McpServerRepository, ToolBindingRepository, ToolboxRepository


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
    endpoint = (server.endpoint or "").strip()
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
