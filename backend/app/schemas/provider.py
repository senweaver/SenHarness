"""Model provider DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.model_provider import ProviderKind
from app.schemas._base import ORMModel, Timestamped


class ProviderCreate(ORMModel):
    kind: ProviderKind
    name: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = Field(
        default=None,
        description="Plaintext API key. Stored envelope-encrypted in Vault; never returned.",
    )
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class ProviderUpdate(ORMModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None


class ProviderRead(Timestamped):
    workspace_id: uuid.UUID
    kind: ProviderKind
    name: str
    base_url: str | None
    default_model: str | None
    enabled: bool
    metadata_json: dict
    has_key: bool = False
