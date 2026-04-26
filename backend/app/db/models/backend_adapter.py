"""BackendAdapter — workspace-scoped registry of remote Agent Runtime adapters.

A single row describes a remote worker (OpenClaw-compatible engine) that can
be bound to an Agent via ``agents.backend_adapter_id``. The ``api_key_hash``
column is the SHA-256 of the raw ``X-Api-Key`` — hot-path auth looks the row
up by that hash without decrypting the Vault item. The ``api_key_vault_id``
points to the encrypted copy for rotation/reveal flows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class BackendAdapterKind(StrEnum):
    OPENCLAW = "openclaw"


class BackendAdapterHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class BackendAdapter(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "backend_adapters"
    __table_args__ = (
        Index(
            "ix_backend_adapters_workspace_kind", "workspace_id", "kind"
        ),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[BackendAdapterKind] = mapped_column(
        String(32), default=BackendAdapterKind.OPENCLAW, nullable=False
    )

    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)

    api_key_vault_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vault_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    # SHA-256 hex digest of the raw X-Api-Key. Unique across workspaces so a
    # collision (astronomically unlikely) fails loudly rather than silently
    # authenticating into the wrong workspace.
    api_key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )

    capabilities_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    health_status: Mapped[BackendAdapterHealth] = mapped_column(
        String(16), default=BackendAdapterHealth.UNKNOWN, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
