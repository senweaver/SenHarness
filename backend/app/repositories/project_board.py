"""Repositories for project boards + cards (M4.4)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import asc, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.board_card import BoardCard, BoardCardColumn
from app.db.models.project_board import ProjectBoard
from app.db.repository import AsyncRepository


class ProjectBoardRepository(AsyncRepository[ProjectBoard]):
    model = ProjectBoard

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProjectBoard)

    async def list_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> Sequence[ProjectBoard]:
        """List boards in a workspace, optionally filtered by squad.

        Passing ``squad_id=None`` returns every board (workspace-level
        + every squad's). To explicitly fetch the workspace-level
        boards (no squad), filter in the service layer; the kanban
        sidebar uses both modes.
        """
        stmt = (
            select(ProjectBoard)
            .where(
                ProjectBoard.workspace_id == workspace_id,
                ProjectBoard.deleted_at.is_(None),
            )
            .order_by(asc(ProjectBoard.created_at))
            .limit(limit)
        )
        if squad_id is not None:
            stmt = stmt.where(ProjectBoard.squad_id == squad_id)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_or_404(self, *, workspace_id: uuid.UUID, board_id: uuid.UUID) -> ProjectBoard:
        row = await self.get(board_id)
        if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
            raise NotFound("board not found", code="board.not_found")
        return row


class BoardCardRepository(AsyncRepository[BoardCard]):
    model = BoardCard

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BoardCard)

    async def list_for_board(
        self,
        *,
        workspace_id: uuid.UUID,
        board_id: uuid.UUID,
        column: BoardCardColumn | None = None,
    ) -> Sequence[BoardCard]:
        stmt = (
            select(BoardCard)
            .where(
                BoardCard.workspace_id == workspace_id,
                BoardCard.board_id == board_id,
                BoardCard.deleted_at.is_(None),
            )
            .order_by(asc(BoardCard.column), asc(BoardCard.sort_order))
        )
        if column is not None:
            stmt = stmt.where(BoardCard.column == column)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_or_404(self, *, workspace_id: uuid.UUID, card_id: uuid.UUID) -> BoardCard:
        row = await self.get(card_id)
        if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
            raise NotFound("card not found", code="card.not_found")
        return row

    async def next_sort_order(
        self,
        *,
        board_id: uuid.UUID,
        column: BoardCardColumn,
    ) -> int:
        """Append slot — one past the highest live ``sort_order`` in column.

        Used by ``create_card`` so a freshly created card lands at the
        bottom of its column without having to re-pack neighbours.
        """
        rows = await self.list_cards_in_column(board_id=board_id, column=column)
        if not rows:
            return 0
        return int(rows[-1].sort_order) + 1

    async def list_cards_in_column(
        self,
        *,
        board_id: uuid.UUID,
        column: BoardCardColumn,
    ) -> Sequence[BoardCard]:
        """All live cards in a single column, ordered by ``sort_order``."""
        stmt = (
            select(BoardCard)
            .where(
                BoardCard.board_id == board_id,
                BoardCard.column == column,
                BoardCard.deleted_at.is_(None),
            )
            .order_by(asc(BoardCard.sort_order), asc(BoardCard.created_at))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def reorder_in_column(
        self,
        *,
        board_id: uuid.UUID,
        column: BoardCardColumn,
        card_id_to_position: dict[uuid.UUID, int],
    ) -> None:
        """Densely renumber every card in a column.

        ``card_id_to_position`` maps each live card in the column to
        its desired 0-based slot. Any card omitted from the map keeps
        its existing relative order at the end of the column. The
        renumbering is dense (0, 1, 2, ...) so the next ``create_card``
        can append cheaply via :meth:`next_sort_order`.
        """
        existing = await self.list_cards_in_column(board_id=board_id, column=column)

        positioned = sorted(
            ((card_id_to_position[c.id], c) for c in existing if c.id in card_id_to_position),
            key=lambda pair: pair[0],
        )
        leftovers = [c for c in existing if c.id not in card_id_to_position]

        ordered: list[BoardCard] = [c for _, c in positioned] + leftovers
        for new_order, card in enumerate(ordered):
            if int(card.sort_order) != new_order:
                card.sort_order = new_order
        if ordered:
            await self.session.flush(ordered)

    async def list_for_agent(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 50,
    ) -> Sequence[BoardCard]:
        """Open cards assigned to a single agent inside the workspace.

        Used by the agent profile / inbox to surface "what is queued
        for me right now". Excludes cards in the ``done`` column so
        completed work doesn't pile up in the inbox view.
        """
        stmt = (
            select(BoardCard)
            .where(
                BoardCard.workspace_id == workspace_id,
                BoardCard.assignee_agent_id == agent_id,
                BoardCard.column != BoardCardColumn.DONE,
                BoardCard.deleted_at.is_(None),
            )
            .order_by(nullslast(asc(BoardCard.due_at)), asc(BoardCard.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
