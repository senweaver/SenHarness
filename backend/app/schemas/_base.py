"""Shared base classes and helpers for Pydantic DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base DTO mapped from ORM objects (`from_attributes=True`)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PagedResponse(ORMModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int


class IdRef(ORMModel):
    id: uuid.UUID


class Envelope(BaseModel):
    """Canonical error envelope (matches ExceptionHandlers output)."""

    code: str
    detail: str
    extras: dict = Field(default_factory=dict)


class Timestamped(ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
