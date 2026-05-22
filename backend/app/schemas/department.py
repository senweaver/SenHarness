"""Department DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas._base import ORMModel


class DepartmentRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    path: str
    created_at: datetime
    updated_at: datetime
    # Populated by /departments — lets the UI show "3 members" without a
    # second round trip.
    member_count: int = 0


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: uuid.UUID | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: uuid.UUID | None = None
