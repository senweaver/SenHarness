"""Unit: M2.5.4 ``McpClientConfig`` validation + transport normalisation.

Pure-function checks — no DB, no Redis, no SDK. These exercise the
guard rails that prevent a misconfigured row from ever reaching the
SDK handshake stage.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.mcp import McpOAuthConfig, McpServerCreate
from app.services.mcp import _normalise_transport, _split_command
from app.services.mcp_client import (
    McpClientConfig,
    McpConfigInvalid,
    McpTransport,
)


# ─── McpClientConfig.validate ───────────────────────────────────
def test_stdio_requires_command():
    config = McpClientConfig(transport=McpTransport.STDIO)
    with pytest.raises(McpConfigInvalid, match="command"):
        config.validate()


def test_stdio_command_parts_must_be_non_empty():
    config = McpClientConfig(
        transport=McpTransport.STDIO,
        command=["python", ""],
    )
    with pytest.raises(McpConfigInvalid):
        config.validate()


def test_stdio_happy_path():
    config = McpClientConfig(
        transport=McpTransport.STDIO,
        command=["python", "-m", "mcp.server"],
    )
    config.validate()


def test_sse_requires_url():
    config = McpClientConfig(transport=McpTransport.SSE)
    with pytest.raises(McpConfigInvalid, match="url"):
        config.validate()


def test_streamable_http_requires_url():
    config = McpClientConfig(transport=McpTransport.STREAMABLE_HTTP, url="   ")
    with pytest.raises(McpConfigInvalid, match="url"):
        config.validate()


def test_sse_happy():
    McpClientConfig(
        transport=McpTransport.SSE,
        url="https://mcp.example.com/sse",
    ).validate()


def test_keepalive_bounds():
    base = dict(transport=McpTransport.SSE, url="https://x.example.com")
    with pytest.raises(McpConfigInvalid):
        McpClientConfig(**base, keepalive_seconds=2).validate()
    with pytest.raises(McpConfigInvalid):
        McpClientConfig(**base, keepalive_seconds=999).validate()


def test_request_timeout_bounds():
    base = dict(transport=McpTransport.SSE, url="https://x.example.com")
    with pytest.raises(McpConfigInvalid):
        McpClientConfig(**base, request_timeout_seconds=10).validate()


def test_max_concurrent_bounds():
    base = dict(transport=McpTransport.SSE, url="https://x.example.com")
    with pytest.raises(McpConfigInvalid):
        McpClientConfig(**base, max_concurrent=0).validate()
    with pytest.raises(McpConfigInvalid):
        McpClientConfig(**base, max_concurrent=64).validate()


# ─── Transport normalisation ────────────────────────────────────
def test_normalise_transport_aliases():
    assert _normalise_transport("stdio") == McpTransport.STDIO
    assert _normalise_transport("command") == McpTransport.STDIO
    assert _normalise_transport("http") == McpTransport.STREAMABLE_HTTP
    assert _normalise_transport("https") == McpTransport.STREAMABLE_HTTP
    assert _normalise_transport("sse") == McpTransport.SSE
    assert _normalise_transport("streamable-http") == McpTransport.STREAMABLE_HTTP
    assert _normalise_transport(None) == McpTransport.STDIO


def test_normalise_transport_rejects_unknown():
    with pytest.raises(McpConfigInvalid):
        _normalise_transport("websocket-magic")


def test_split_command_handles_args_json():
    class _Stub:
        command = "npx -y @modelcontextprotocol/server-github"
        args_json = ["--config", "secret"]

    parts = _split_command(_Stub())  # type: ignore[arg-type]
    assert parts[:3] == ["npx", "-y", "@modelcontextprotocol/server-github"]
    assert parts[-2:] == ["--config", "secret"]


def test_split_command_empty_when_no_command():
    class _Stub:
        command = ""
        args_json = []

    assert _split_command(_Stub()) == []  # type: ignore[arg-type]


# ─── McpServerCreate cross-field rules ──────────────────────────
def test_create_stdio_requires_command():
    with pytest.raises(ValidationError):
        McpServerCreate(name="x", slug="x", transport="stdio", command=None)


def test_create_sse_requires_url():
    with pytest.raises(ValidationError):
        McpServerCreate(name="x", slug="x", transport="sse")


def test_create_streamable_http_requires_url():
    with pytest.raises(ValidationError):
        McpServerCreate(name="x", slug="x", transport="streamable_http")


def test_create_streamable_http_happy():
    body = McpServerCreate(
        name="GitHub",
        slug="gh",
        transport="streamable_http",
        url="https://gh.example.com/mcp",
    )
    assert body.transport == "streamable_http"
    assert body.url


# ─── McpOAuthConfig schema ──────────────────────────────────────
def test_oauth_requires_secret_or_ref():
    with pytest.raises(ValidationError):
        McpOAuthConfig(
            client_id="abc",
            token_url="https://idp.example.com/token",
        )


def test_oauth_inline_secret_passes():
    cfg = McpOAuthConfig(
        client_id="abc",
        client_secret="hush",
        token_url="https://idp.example.com/token",
        scopes=["read:tools", "call:tools"],
    )
    assert cfg.client_secret == "hush"
    assert cfg.refresh_grace_seconds == 300


def test_oauth_vault_ref_passes_without_inline():
    cfg = McpOAuthConfig(
        client_id="abc",
        client_secret_ref="${vault://workspace/foo}",
        token_url="https://idp.example.com/token",
    )
    assert cfg.client_secret is None
    assert cfg.client_secret_ref


def test_oauth_scope_strips_whitespace_and_rejects_spaced():
    cfg = McpOAuthConfig(
        client_id="abc",
        client_secret_ref="${vault://workspace/x}",
        token_url="https://idp.example.com/token",
        scopes=["  read:tools  ", "call:tools"],
    )
    assert cfg.scopes == ["read:tools", "call:tools"]
    with pytest.raises(ValidationError):
        McpOAuthConfig(
            client_id="abc",
            client_secret_ref="${vault://workspace/x}",
            token_url="https://idp.example.com/token",
            scopes=["read tools"],
        )


def test_oauth_refresh_grace_bounds():
    with pytest.raises(ValidationError):
        McpOAuthConfig(
            client_id="abc",
            client_secret_ref="${vault://workspace/x}",
            token_url="https://idp.example.com/token",
            refresh_grace_seconds=10,
        )
    with pytest.raises(ValidationError):
        McpOAuthConfig(
            client_id="abc",
            client_secret_ref="${vault://workspace/x}",
            token_url="https://idp.example.com/token",
            refresh_grace_seconds=4000,
        )
