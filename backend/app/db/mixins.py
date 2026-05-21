"""Reusable ORM mixins: id/uuid, timestamps, soft-delete, workspace-scope."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class UuidPkMixin:
    """UUID primary key (uuid4). Alembic friendly."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    """`created_at` / `updated_at` (server-side defaults)."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Logical delete (set `deleted_at`, filter `deleted_at IS NULL` by default)."""

    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class WorkspaceScopedMixin:
    """Tenant isolation. Every scoped entity carries `workspace_id` + FK."""

    @declared_attr
    @classmethod
    def workspace_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
