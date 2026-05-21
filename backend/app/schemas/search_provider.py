"""Search provider DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped


class SearchProviderCreate(ORMModel):
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    enabled: bool = True
    priority: int = 100
    api_key: str | None = Field(
        default=None,
        description="Plaintext API key — stored envelope-encrypted in Vault.",
    )
    metadata_json: dict = Field(default_factory=dict)


class SearchProviderUpdate(ORMModel):
    name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    api_key: str | None = None
    metadata_json: dict | None = None


class SearchProviderRead(Timestamped):
    workspace_id: uuid.UUID
    kind: str
    name: str
    base_url: str | None
    enabled: bool
    priority: int
    metadata_json: dict
    has_key: bool = False
