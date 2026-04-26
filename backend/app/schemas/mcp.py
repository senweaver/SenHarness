"""MCP server and toolbox DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.mcp import McpServerHealth
from app.schemas._base import ORMModel, Timestamped


class McpServerCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    transport: str = Field(default="http", max_length=32)
    endpoint: str | None = Field(default=None, max_length=1024)
    command: str | None = Field(default=None, max_length=512)
    args_json: list = Field(default_factory=list)
    env_json: dict = Field(default_factory=dict)
    auth_json: dict = Field(default_factory=dict)
    capabilities_json: dict = Field(default_factory=dict)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class McpServerUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    slug: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    transport: str | None = Field(default=None, max_length=32)
    endpoint: str | None = Field(default=None, max_length=1024)
    command: str | None = Field(default=None, max_length=512)
    args_json: list | None = None
    env_json: dict | None = None
    auth_json: dict | None = None
    capabilities_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None


class McpServerRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    slug: str
    transport: str
    endpoint: str | None
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
