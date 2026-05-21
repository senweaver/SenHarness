"""Workspace ↔ hub-pack subscription record (M3.1).

A subscription is the workspace's stated intent to track a hub pack.
M3.1 only persists the row + auto-pull flag; the M3.3 pull pipeline
will use ``last_pulled_version_no`` / ``last_pulled_at`` to decide
whether the workspace already has the latest hub version (and to
draft a new local CANDIDATE when ``auto_pull=true`` and the hub
ships a newer is_active=true row).

A workspace can subscribe at most once per hub pack — re-clicking
"subscribe" toggles ``auto_pull`` rather than creating a duplicate
row. The unique constraint enforces that.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class WorkspaceHubSubscription(
    UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "workspace_hub_subscriptions"

    hub_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hub_skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    auto_pull: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    last_pulled_version_no: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    last_pulled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    subscribed_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "hub_pack_id",
            name="uq_workspace_hub_subscriptions_ws_pack",
        ),
    )
