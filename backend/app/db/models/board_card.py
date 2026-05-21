"""Project Kanban card (M4.4).

A ``BoardCard`` is a single planner item inside a
:class:`~app.db.models.project_board.ProjectBoard`. The ``column`` /
``priority`` enums are deliberately fixed in V1 — no custom columns,
no free-form priority strings — so the UI can render a stable layout
and search/aggregate queries don't have to special-case tenant-local
schemas.

Assignees come in two flavours:

* :attr:`assignee_agent_id` — an :class:`~app.db.models.agent.Agent`
  inside the same workspace. This is the path that lets a card flow
  into the Squad / planner story: an agent can pick up a card the way
  a teammate would.
* :attr:`assignee_identity_id` — a human :class:`~app.db.models.identity.Identity`.
  Both assignees may be set simultaneously (an agent paired with the
  human accountable for the outcome), but neither is required.

``sort_order`` is a per-column integer the API maintains via
:meth:`app.repositories.project_board.BoardCardRepository.reorder_in_column`.
Lower values render first. The composite index
``(board_id, column, sort_order)`` covers the dominant board query —
"give me every card in column X for board Y, sorted" — so the kanban
read path is a single index seek.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class BoardCardColumn(StrEnum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class BoardCardPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


BOARD_CARD_COLUMN_VALUES: tuple[str, ...] = tuple(c.value for c in BoardCardColumn)
BOARD_CARD_PRIORITY_VALUES: tuple[str, ...] = tuple(p.value for p in BoardCardPriority)


class BoardCard(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "board_cards"
    __table_args__ = (
        Index(
            "ix_board_cards_board_column_sort",
            "board_id",
            "column",
            "sort_order",
        ),
    )

    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    column: Mapped[BoardCardColumn] = mapped_column(
        SAEnum(
            BoardCardColumn,
            native_enum=False,
            length=20,
            name="board_card_column",
        ),
        default=BoardCardColumn.BACKLOG,
        server_default=BoardCardColumn.BACKLOG.value,
        nullable=False,
        index=True,
    )

    priority: Mapped[BoardCardPriority] = mapped_column(
        SAEnum(
            BoardCardPriority,
            native_enum=False,
            length=16,
            name="board_card_priority",
        ),
        default=BoardCardPriority.NORMAL,
        server_default=BoardCardPriority.NORMAL.value,
        nullable=False,
    )

    assignee_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assignee_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
