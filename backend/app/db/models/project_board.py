"""Project Kanban board (M4.4).

A ``ProjectBoard`` is a workspace-scoped lightweight planner row that
groups :class:`~app.db.models.board_card.BoardCard` rows into the four
fixed kanban columns (``backlog`` / ``in_progress`` / ``review`` /
``done``). Boards anchor to either a workspace or — optionally — a
specific squad: ``squad_id IS NULL`` means the board lives at the
workspace level (the fallback ``/workspace/board`` route renders these);
``squad_id`` set means it belongs to that squad and the canonical UI
path is ``/squads/{id}/board``.

Workspace-scoped + soft-delete + timestamp mixin so the M0.11 GDPR
cascade picks the table up via :data:`app.services.retention.CASCADE_TARGETS`
and the daily physical purge eventually hard-deletes archived rows
past their retention window. The unique constraint on
``(workspace_id, name)`` is workspace-local — different tenants can
both have a "Backlog" board without collision.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class ProjectBoard(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "project_boards"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "name", name="uq_project_boards_workspace_name"
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    squad_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("squads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
