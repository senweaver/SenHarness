"""Project kanban service (M4.4).

Owns the transactions for ``project_boards`` + ``board_cards`` and the
sort-order arithmetic that keeps each column densely packed. Routes
delegate every multi-step write here so audit + invariant enforcement
stay in one place.

Sort algorithm (move_card):

1. Resolve the card and its (current_column, current_sort_order).
2. Build the live list of cards in the target column **without** the
   moving card.
3. Insert a placeholder at ``target_position`` (clamped to
   ``[0, len(list)]``) and densely renumber 0..N-1.
4. Apply the renumbered slots to each card; the moving card receives
   ``target_position`` and its column is updated.

This is O(n) in the column size — well under the realistic upper bound
for a kanban planner. Postgres covers reads via the composite
``(board_id, column, sort_order)`` index.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import utcnow_naive
from app.db.models.agent import Agent
from app.db.models.board_card import BoardCard, BoardCardColumn, BoardCardPriority
from app.db.models.project_board import ProjectBoard
from app.db.models.squad import Squad
from app.repositories.agent import AgentRepository
from app.repositories.identity import IdentityRepository
from app.repositories.project_board import (
    BoardCardRepository,
    ProjectBoardRepository,
)


# ─── Boards ─────────────────────────────────────────────────────
async def list_boards(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad_id: uuid.UUID | None = None,
    limit: int = 100,
) -> Sequence[ProjectBoard]:
    return await ProjectBoardRepository(db).list_for_workspace(
        workspace_id=workspace_id, squad_id=squad_id, limit=limit
    )


async def get_board(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    board_id: uuid.UUID,
) -> ProjectBoard:
    return await ProjectBoardRepository(db).get_or_404(
        workspace_id=workspace_id, board_id=board_id
    )


async def create_board(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    description: str | None,
    squad_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None,
) -> ProjectBoard:
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValidationFailed("board name required", code="board.name_required")

    if squad_id is not None:
        await _ensure_squad_in_workspace(
            db, workspace_id=workspace_id, squad_id=squad_id
        )

    repo = ProjectBoardRepository(db)
    if await repo.get_by(workspace_id=workspace_id, name=name_clean):
        raise Conflict(
            f"board with name {name_clean!r} already exists",
            code="board.name_taken",
        )

    return await repo.create(
        workspace_id=workspace_id,
        name=name_clean,
        description=(description or None),
        squad_id=squad_id,
        created_by=actor_identity_id,
    )


async def update_board(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    board_id: uuid.UUID,
    **patch: Any,
) -> ProjectBoard:
    repo = ProjectBoardRepository(db)
    board = await repo.get_or_404(
        workspace_id=workspace_id, board_id=board_id
    )

    fields: dict[str, Any] = {}
    if "name" in patch and patch["name"] is not None:
        new_name = str(patch["name"]).strip()
        if not new_name:
            raise ValidationFailed(
                "board name required", code="board.name_required"
            )
        if new_name != board.name:
            existing = await repo.get_by(
                workspace_id=workspace_id, name=new_name
            )
            if existing is not None and existing.id != board.id:
                raise Conflict(
                    f"board with name {new_name!r} already exists",
                    code="board.name_taken",
                )
        fields["name"] = new_name

    if "description" in patch:
        desc = patch["description"]
        fields["description"] = desc if desc else None

    if "squad_id" in patch:
        new_squad_id = patch["squad_id"]
        if new_squad_id is not None:
            await _ensure_squad_in_workspace(
                db, workspace_id=workspace_id, squad_id=new_squad_id
            )
        fields["squad_id"] = new_squad_id

    if not fields:
        return board
    return await repo.update(board, **fields)


async def archive_board(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    board_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> ProjectBoard:
    """Soft-delete the board. Cards stay alive at the DB level and
    are re-exposed if the board is ever restored manually; the kanban
    UI hides the board immediately. The M0.11 GDPR cascade or daily
    purge eventually hard-deletes both rows."""
    _ = actor_identity_id
    repo = ProjectBoardRepository(db)
    board = await repo.get_or_404(
        workspace_id=workspace_id, board_id=board_id
    )
    if board.deleted_at is None:
        await repo.soft_delete(board)
    return board


# ─── Cards ──────────────────────────────────────────────────────
async def list_cards(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    board_id: uuid.UUID,
    column: BoardCardColumn | None = None,
) -> Sequence[BoardCard]:
    await ProjectBoardRepository(db).get_or_404(
        workspace_id=workspace_id, board_id=board_id
    )
    return await BoardCardRepository(db).list_for_board(
        workspace_id=workspace_id, board_id=board_id, column=column
    )


async def get_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
) -> BoardCard:
    return await BoardCardRepository(db).get_or_404(
        workspace_id=workspace_id, card_id=card_id
    )


async def create_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    board_id: uuid.UUID,
    title: str,
    description: str | None = None,
    column: BoardCardColumn = BoardCardColumn.BACKLOG,
    priority: BoardCardPriority = BoardCardPriority.NORMAL,
    assignee_agent_id: uuid.UUID | None = None,
    assignee_identity_id: uuid.UUID | None = None,
    due_at: datetime | None = None,
    actor_identity_id: uuid.UUID | None = None,
) -> BoardCard:
    title_clean = (title or "").strip()
    if not title_clean:
        raise ValidationFailed("card title required", code="card.title_required")

    await ProjectBoardRepository(db).get_or_404(
        workspace_id=workspace_id, board_id=board_id
    )

    if assignee_agent_id is not None:
        await _ensure_agent_in_workspace(
            db, workspace_id=workspace_id, agent_id=assignee_agent_id
        )
    if assignee_identity_id is not None:
        await _ensure_identity_exists(db, identity_id=assignee_identity_id)

    cards_repo = BoardCardRepository(db)
    next_sort = await cards_repo.next_sort_order(
        board_id=board_id, column=column
    )
    return await cards_repo.create(
        workspace_id=workspace_id,
        board_id=board_id,
        title=title_clean,
        description=(description or None),
        column=column,
        priority=priority,
        assignee_agent_id=assignee_agent_id,
        assignee_identity_id=assignee_identity_id,
        sort_order=next_sort,
        due_at=due_at,
        created_by=actor_identity_id,
    )


async def update_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    **patch: Any,
) -> BoardCard:
    cards_repo = BoardCardRepository(db)
    card = await cards_repo.get_or_404(
        workspace_id=workspace_id, card_id=card_id
    )

    fields: dict[str, Any] = {}
    if "title" in patch and patch["title"] is not None:
        new_title = str(patch["title"]).strip()
        if not new_title:
            raise ValidationFailed(
                "card title required", code="card.title_required"
            )
        fields["title"] = new_title

    if "description" in patch:
        desc = patch["description"]
        fields["description"] = desc if desc else None

    if "priority" in patch and patch["priority"] is not None:
        fields["priority"] = patch["priority"]

    if "assignee_agent_id" in patch:
        new_agent_id = patch["assignee_agent_id"]
        if new_agent_id is not None:
            await _ensure_agent_in_workspace(
                db, workspace_id=workspace_id, agent_id=new_agent_id
            )
        fields["assignee_agent_id"] = new_agent_id

    if "assignee_identity_id" in patch:
        new_identity_id = patch["assignee_identity_id"]
        if new_identity_id is not None:
            await _ensure_identity_exists(db, identity_id=new_identity_id)
        fields["assignee_identity_id"] = new_identity_id

    if "due_at" in patch:
        fields["due_at"] = patch["due_at"]

    if not fields:
        return card
    return await cards_repo.update(card, **fields)


async def move_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    target_column: BoardCardColumn,
    target_position: int,
    actor_identity_id: uuid.UUID | None,
) -> BoardCard:
    """Move a card to ``(target_column, target_position)`` and re-pack.

    The renumbering is single-column scoped: only the source column
    (when different from target) and the target column are touched.
    Sort orders end up dense (0..N-1) in the target column so the
    next append is cheap.
    """
    _ = actor_identity_id
    if target_position < 0:
        raise ValidationFailed(
            "target_position must be non-negative",
            code="card.invalid_position",
        )

    cards_repo = BoardCardRepository(db)
    card = await cards_repo.get_or_404(
        workspace_id=workspace_id, card_id=card_id
    )

    source_column = card.column
    source_board = card.board_id

    target_existing = list(
        await cards_repo.list_cards_in_column(
            board_id=source_board, column=target_column
        )
    )
    if source_column == target_column:
        target_existing = [c for c in target_existing if c.id != card.id]

    clamped = max(0, min(target_position, len(target_existing)))
    target_existing.insert(clamped, card)

    for new_order, c in enumerate(target_existing):
        if int(c.sort_order) != new_order:
            c.sort_order = new_order
    card.column = target_column

    if source_column != target_column:
        source_remaining = list(
            await cards_repo.list_cards_in_column(
                board_id=source_board, column=source_column
            )
        )
        source_remaining = [c for c in source_remaining if c.id != card.id]
        for new_order, c in enumerate(source_remaining):
            if int(c.sort_order) != new_order:
                c.sort_order = new_order
        if source_remaining:
            await db.flush(source_remaining)

    if target_existing:
        await db.flush(target_existing)
    await db.refresh(card)
    return card


async def archive_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> BoardCard:
    _ = actor_identity_id
    cards_repo = BoardCardRepository(db)
    card = await cards_repo.get_or_404(
        workspace_id=workspace_id, card_id=card_id
    )
    if card.deleted_at is None:
        await cards_repo.soft_delete(card)
    return card


async def complete_card(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> BoardCard:
    """Mark a card complete: column → ``done``, ``completed_at`` stamped now.

    Uses :func:`move_card` to push the card to the bottom of the
    ``done`` column so the latest completions appear at the end. Idempotent
    when the card is already in ``done``.
    """
    cards_repo = BoardCardRepository(db)
    card = await cards_repo.get_or_404(
        workspace_id=workspace_id, card_id=card_id
    )
    if card.column != BoardCardColumn.DONE:
        done_existing = await cards_repo.list_cards_in_column(
            board_id=card.board_id, column=BoardCardColumn.DONE
        )
        await move_card(
            db,
            workspace_id=workspace_id,
            card_id=card.id,
            target_column=BoardCardColumn.DONE,
            target_position=len(done_existing),
            actor_identity_id=actor_identity_id,
        )
        await db.refresh(card)
    if card.completed_at is None:
        card.completed_at = utcnow_naive()
        await db.flush([card])
    return card


async def list_cards_for_agent(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    limit: int = 50,
) -> Sequence[BoardCard]:
    await _ensure_agent_in_workspace(
        db, workspace_id=workspace_id, agent_id=agent_id
    )
    return await BoardCardRepository(db).list_for_agent(
        workspace_id=workspace_id, agent_id=agent_id, limit=limit
    )


# ─── Internal validators ────────────────────────────────────────
async def _ensure_squad_in_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID, squad_id: uuid.UUID
) -> Squad:
    from app.repositories.squad import SquadRepository

    squad = await SquadRepository(db).get(squad_id)
    if (
        squad is None
        or squad.workspace_id != workspace_id
        or squad.deleted_at is not None
    ):
        raise NotFound("squad not found", code="squad.not_found")
    return squad


async def _ensure_agent_in_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
) -> Agent:
    agent = await AgentRepository(db).get(agent_id)
    if (
        agent is None
        or agent.workspace_id != workspace_id
        or agent.deleted_at is not None
    ):
        raise NotFound("agent not found", code="agent.not_found")
    return agent


async def _ensure_identity_exists(
    db: AsyncSession, *, identity_id: uuid.UUID
) -> None:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity not found", code="identity.not_found")
