"""Generic secrets DTOs — used by Vault UI.

Distinct from ``provider.py`` (model API keys wired into LLM providers). These
are workspace-scope arbitrary secrets an agent can reference at runtime.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas._base import ORMModel


class SecretRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    kind: str
    required_approval: bool
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class SecretCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=16_384)
    kind: str = Field(default="generic", max_length=32)
    metadata_json: dict = Field(default_factory=dict)
    required_approval: bool = False


class SecretUpdate(BaseModel):
    value: str | None = Field(default=None, max_length=16_384)
    metadata_json: dict | None = None
    required_approval: bool | None = None
