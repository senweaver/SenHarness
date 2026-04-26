"""Unit tests for MCP service health checks."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.db.models.mcp import McpServerHealth
from app.services.mcp import ping_server


def _server(**overrides):
    base = {
        "enabled": True,
        "transport": "http",
        "endpoint": "http://localhost:8080",
        "command": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_ping_disabled_server():
    status, detail = await ping_server(_server(enabled=False))
    assert status == McpServerHealth.DOWN
    assert "disabled" in detail


@pytest.mark.asyncio
async def test_ping_stdio_requires_command():
    status, detail = await ping_server(_server(transport="stdio", command=None))
    assert status == McpServerHealth.DOWN
    assert "missing command" in detail


@pytest.mark.asyncio
async def test_ping_stdio_with_command_is_unknown():
    status, detail = await ping_server(_server(transport="stdio", command="python -m mcp.server"))
    assert status == McpServerHealth.UNKNOWN
    assert "configured" in detail


@pytest.mark.asyncio
async def test_ping_http_failure(monkeypatch):
    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.services.mcp.httpx.AsyncClient", lambda timeout: _BrokenClient())

    status, detail = await ping_server(_server(endpoint="http://invalid"))
    assert status == McpServerHealth.DOWN
    assert "boom" in detail
