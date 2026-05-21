"""Pydantic DTOs for the project kanban (M4.4).

Three layers:

* :class:`ProjectBoardCreate` / :class:`ProjectBoardUpdate` /
  :class:`ProjectBoardRead` — the board envelope itself. Boards live
  at the workspace level by default; setting ``squad_id`` rebases the
  board onto a specific squad's planner page.
* :class:`BoardCardCreate` / :class:`BoardCardUpdate` /
  :class:`BoardCardMove` / :class:`BoardCardRead` — individual cards.
* :class:`BoardKanbanRead` — the full board snapshot the kanban page
  uses to render columns in one round trip.

The fixed four-column / four-priority enums match the ORM enums
exactly. V1 deliberately rejects custom columns — the UI's mental
model is "Backlog → In Progress → Review → Done" and downstream
analytics queries depend on the closed set.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.board_card import BoardCardColumn, BoardCardPriority
from app.schemas._base import ORMModel, Timestamped


class ProjectBoardCreate(ORMModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    squad_id: uuid.UUID | None = None


class ProjectBoardUpdate(ORMModel):
    """Patch payload — every field optional. ``None`` means leave alone."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    squad_id: uuid.UUID | None = None


class ProjectBoardRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    squad_id: uuid.UUID | None
    created_by: uuid.UUID | None


class BoardCardCreate(ORMModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    column: BoardCardColumn = BoardCardColumn.BACKLOG
    priority: BoardCardPriority = BoardCardPriority.NORMAL
    assignee_agent_id: uuid.UUID | None = None
    assignee_identity_id: uuid.UUID | None = None
    due_at: datetime | None = None


class BoardCardUpdate(ORMModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    priority: BoardCardPriority | None = None
    assignee_agent_id: uuid.UUID | None = None
    assignee_identity_id: uuid.UUID | None = None
    due_at: datetime | None = None


class BoardCardMove(ORMModel):
    """Single-step kanban reorder.

    ``target_position`` is the 0-based slot inside ``target_column``
    after the move. The service translates this into a fresh
    ``sort_order`` value and rewrites neighbours so the column stays
    densely packed.
    """

    target_column: BoardCardColumn
    target_position: int = Field(ge=0)


class BoardCardRead(Timestamped):
    workspace_id: uuid.UUID
    board_id: uuid.UUID
    title: str
    description: str | None
    column: BoardCardColumn
    priority: BoardCardPriority
    assignee_agent_id: uuid.UUID | None
    assignee_identity_id: uuid.UUID | None
    sort_order: int
    due_at: datetime | None
    completed_at: datetime | None
    created_by: uuid.UUID | None


class BoardKanbanRead(ORMModel):
    """Snapshot the kanban page hydrates in a single fetch.

    ``columns`` is keyed by :class:`BoardCardColumn` value — the
    frontend renders the four buckets in the canonical order Backlog
    → In Progress → Review → Done regardless of dictionary iteration
    order.
    """

    board: ProjectBoardRead
    columns: dict[str, list[BoardCardRead]]
