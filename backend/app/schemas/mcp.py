"""MCP server and toolbox DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.db.models.mcp import McpServerHealth
from app.schemas._base import ORMModel, Timestamped

TransportLiteral = Literal["stdio", "sse", "streamable_http"]


class McpOAuthConfig(ORMModel):
    """OAuth configuration captured on an MCP server row.

    The plaintext ``client_secret`` is rotated into the workspace vault
    on the first dance; subsequent updates re-seal under a fresh
    workspace key so the JSONB column never carries a usable secret in
    audit dumps. ``token_url`` is the IdP's token endpoint and
    ``scopes`` is the requested OAuth scope list (joined to a single
    space-delimited string when shipped to the IdP).
    """

    client_id: str = Field(min_length=1, max_length=200)
    client_secret_ref: str | None = Field(
        default=None,
        description="``vault://workspace/<name>`` template — preferred over inline secret.",
        max_length=300,
    )
    client_secret: str | None = Field(
        default=None,
        description="Plaintext secret. Migrated into the vault on the first save.",
        max_length=400,
    )
    token_url: str = Field(min_length=8, max_length=500)
    scopes: list[str] = Field(default_factory=list, max_length=20)
    refresh_grace_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("scopes")
    @classmethod
    def _scope_shape(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for scope in value:
            scope = scope.strip()
            if not scope:
                continue
            if " " in scope or len(scope) > 100:
                raise ValueError("scope must be ≤100 chars and contain no whitespace")
            cleaned.append(scope)
        return cleaned

    @model_validator(mode="after")
    def _has_secret_source(self) -> "McpOAuthConfig":
        if not self.client_secret_ref and not self.client_secret:
            raise ValueError(
                "either ``client_secret`` (one-time, rotates into vault) or "
                "``client_secret_ref`` (vault template) must be supplied"
            )
        return self


def _validate_transport_payload(
    transport: str | None,
    *,
    url: str | None,
    command: str | None,
    auth_json: dict | None,
) -> None:
    """Cross-field invariants shared by Create + Update."""
    if transport is None:
        return
    if transport == "stdio":
        if not command:
            raise ValueError("stdio transport requires ``command``")
        return
    if transport in ("sse", "streamable_http"):
        if not url:
            raise ValueError(f"{transport} transport requires ``url``")
        return
    raise ValueError(f"unknown transport {transport!r}")


class McpServerCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    transport: TransportLiteral = Field(default="stdio")
    endpoint: str | None = Field(default=None, max_length=1024)
    url: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=512)
    args_json: list = Field(default_factory=list)
    env_json: dict = Field(default_factory=dict)
    auth_json: dict = Field(default_factory=dict)
    auth_oauth: McpOAuthConfig | None = None
    capabilities_json: dict = Field(default_factory=dict)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "McpServerCreate":
        _validate_transport_payload(
            self.transport,
            url=self.url,
            command=self.command,
            auth_json=self.auth_json,
        )
        return self


class McpServerUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    slug: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    transport: TransportLiteral | None = Field(default=None)
    endpoint: str | None = Field(default=None, max_length=1024)
    url: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=512)
    args_json: list | None = None
    env_json: dict | None = None
    auth_json: dict | None = None
    auth_oauth: McpOAuthConfig | None = None
    capabilities_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None

    @model_validator(mode="after")
    def _validate(self) -> "McpServerUpdate":
        if self.transport is not None:
            _validate_transport_payload(
                self.transport,
                url=self.url,
                command=self.command,
                auth_json=self.auth_json,
            )
        return self


class McpServerRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    slug: str
    transport: str
    endpoint: str | None
    url: str | None
    command: str | None
    args_json: list
    env_json: dict
    auth_json: dict
    capabilities_json: dict
    health_status: McpServerHealth
    last_checked_at: datetime | None = None
    enabled: bool
    metadata_json: dict
    created_by: uuid.UUID | None


class McpServerHealthRead(ORMModel):
    status: McpServerHealth
    detail: str


class ToolboxCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class ToolboxUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    metadata_json: dict | None = None


class ToolboxRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    enabled: bool
    metadata_json: dict
    created_by: uuid.UUID | None


class ToolBindingCreate(ORMModel):
    toolbox_id: uuid.UUID
    mcp_server_id: uuid.UUID | None = None
    tool_name: str = Field(min_length=1, max_length=128)
    alias: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10_000)
    config_json: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)


class ToolBindingUpdate(ORMModel):
    toolbox_id: uuid.UUID | None = None
    mcp_server_id: uuid.UUID | None = None
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    alias: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    config_json: dict | None = None
    metadata_json: dict | None = None


class ToolBindingRead(Timestamped):
    workspace_id: uuid.UUID
    toolbox_id: uuid.UUID
    mcp_server_id: uuid.UUID | None
    tool_name: str
    alias: str | None
    enabled: bool
    priority: int
    config_json: dict
    metadata_json: dict


class AgentToolboxBindIn(ORMModel):
    toolbox_id: uuid.UUID | None = None
