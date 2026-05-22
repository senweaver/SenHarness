"""Project Kanban REST surface (M4.4).

Thirteen routes split between board-shaped and card-shaped paths:

* ``/boards`` — list / create / retrieve / update / archive boards.
  Boards are workspace-admin to create / update / archive but every
  workspace member can list and read them.
* ``/boards/{id}/cards`` — list / create cards inside a board.
* ``/cards/{id}`` — retrieve / update / move / archive / complete a
  single card. Workspace members own these — the kanban is a
  collaborative tool, not an admin-only planner.
* ``/agents/{id}/cards`` — workspace-member-readable inbox of open
  cards assigned to a specific agent.

Rate-limit buckets are split by mutation cadence: list reads are
generous (60/60s), writes are 30 or 60/60s, and the high-frequency
``move`` endpoint gets its own 120/60s budget so dragging cards
around the board doesn't trip the writer cap.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.board_card import BoardCardColumn
from app.schemas.project_board import (
    BoardCardCreate,
    BoardCardMove,
    BoardCardRead,
    BoardCardUpdate,
    BoardKanbanRead,
    ProjectBoardCreate,
    ProjectBoardRead,
    ProjectBoardUpdate,
)
from app.services import audit as audit_svc
from app.services import project_board as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _columns_snapshot(cards) -> dict[str, list[BoardCardRead]]:
    """Project a flat card list into the four-column dict the UI uses."""
    out: dict[str, list[BoardCardRead]] = {col.value: [] for col in BoardCardColumn}
    for c in cards:
        out[c.column.value].append(BoardCardRead.model_validate(c))
    return out


# ─── Boards ─────────────────────────────────────────────────────
@router.get(
    "/boards",
    response_model=list[ProjectBoardRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("boards_list", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def list_boards(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    squad_id: uuid.UUID | None = Query(default=None),
) -> list[ProjectBoardRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_boards(db, workspace_id=ws_id, squad_id=squad_id)
    return [ProjectBoardRead.model_validate(r) for r in rows]


@router.post(
    "/boards",
    response_model=ProjectBoardRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("boards_write", limit=30, period_seconds=60))],
    tags=["project_boards"],
)
async def create_board(
    body: ProjectBoardCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ProjectBoardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    board = await svc.create_board(
        db,
        workspace_id=ws_id,
        name=body.name,
        description=body.description,
        squad_id=body.squad_id,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="board.created",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="project_board",
        resource_id=board.id,
        summary=f"created board {board.name!r}",
        metadata={"squad_id": str(board.squad_id) if board.squad_id else None},
        request=request,
    )
    await db.commit()
    return ProjectBoardRead.model_validate(board)


@router.get(
    "/boards/{board_id}",
    response_model=BoardKanbanRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("boards_list", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def get_board(
    board_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BoardKanbanRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    board = await svc.get_board(db, workspace_id=ws_id, board_id=board_id)
    cards = await svc.list_cards(db, workspace_id=ws_id, board_id=board_id)
    return BoardKanbanRead(
        board=ProjectBoardRead.model_validate(board),
        columns=_columns_snapshot(cards),
    )


@router.patch(
    "/boards/{board_id}",
    response_model=ProjectBoardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("boards_write", limit=30, period_seconds=60))],
    tags=["project_boards"],
)
async def update_board(
    board_id: uuid.UUID,
    body: ProjectBoardUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ProjectBoardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    board = await svc.update_board(
        db,
        workspace_id=ws_id,
        board_id=board_id,
        **body.model_dump(exclude_unset=True),
    )
    await audit_svc.record(
        db,
        action="board.updated",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="project_board",
        resource_id=board.id,
        summary=f"updated board {board.name!r}",
        metadata={"fields": list(body.model_dump(exclude_unset=True).keys())},
        request=request,
    )
    await db.commit()
    return ProjectBoardRead.model_validate(board)


@router.post(
    "/boards/{board_id}/archive",
    response_model=ProjectBoardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("boards_write", limit=30, period_seconds=60))],
    tags=["project_boards"],
)
async def archive_board(
    board_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ProjectBoardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    board = await svc.archive_board(
        db,
        workspace_id=ws_id,
        board_id=board_id,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="board.archived",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="project_board",
        resource_id=board.id,
        summary=f"archived board {board.name!r}",
        request=request,
    )
    await db.commit()
    return ProjectBoardRead.model_validate(board)


# ─── Cards ──────────────────────────────────────────────────────
@router.get(
    "/boards/{board_id}/cards",
    response_model=list[BoardCardRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_list", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def list_board_cards(
    board_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    column: BoardCardColumn | None = Query(default=None),
) -> list[BoardCardRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_cards(db, workspace_id=ws_id, board_id=board_id, column=column)
    return [BoardCardRead.model_validate(r) for r in rows]


@router.post(
    "/boards/{board_id}/cards",
    response_model=BoardCardRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("cards_write", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def create_board_card(
    board_id: uuid.UUID,
    body: BoardCardCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    card = await svc.create_card(
        db,
        workspace_id=ws_id,
        board_id=board_id,
        title=body.title,
        description=body.description,
        column=body.column,
        priority=body.priority,
        assignee_agent_id=body.assignee_agent_id,
        assignee_identity_id=body.assignee_identity_id,
        due_at=body.due_at,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="card.created",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="board_card",
        resource_id=card.id,
        summary=f"created card {card.title!r}",
        metadata={
            "board_id": str(card.board_id),
            "column": card.column.value,
            "priority": card.priority.value,
            "assignee_agent_id": str(card.assignee_agent_id) if card.assignee_agent_id else None,
        },
        request=request,
    )
    await db.commit()
    return BoardCardRead.model_validate(card)


@router.get(
    "/cards/{card_id}",
    response_model=BoardCardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_list", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def get_card(
    card_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    card = await svc.get_card(db, workspace_id=ws_id, card_id=card_id)
    return BoardCardRead.model_validate(card)


@router.patch(
    "/cards/{card_id}",
    response_model=BoardCardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_write", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def update_card(
    card_id: uuid.UUID,
    body: BoardCardUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    card = await svc.update_card(
        db,
        workspace_id=ws_id,
        card_id=card_id,
        **body.model_dump(exclude_unset=True),
    )
    await audit_svc.record(
        db,
        action="card.updated",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="board_card",
        resource_id=card.id,
        summary=f"updated card {card.title!r}",
        metadata={"fields": list(body.model_dump(exclude_unset=True).keys())},
        request=request,
    )
    await db.commit()
    return BoardCardRead.model_validate(card)


@router.post(
    "/cards/{card_id}/move",
    response_model=BoardCardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_move", limit=120, period_seconds=60))],
    tags=["project_boards"],
)
async def move_card(
    card_id: uuid.UUID,
    body: BoardCardMove,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    before = await svc.get_card(db, workspace_id=ws_id, card_id=card_id)
    previous_column = before.column.value
    card = await svc.move_card(
        db,
        workspace_id=ws_id,
        card_id=card_id,
        target_column=body.target_column,
        target_position=body.target_position,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="card.moved",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="board_card",
        resource_id=card.id,
        summary=f"moved card {card.title!r} to {card.column.value}",
        metadata={
            "from_column": previous_column,
            "to_column": card.column.value,
            "to_position": int(card.sort_order),
        },
        request=request,
    )
    await db.commit()
    return BoardCardRead.model_validate(card)


@router.post(
    "/cards/{card_id}/archive",
    response_model=BoardCardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_write", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def archive_card(
    card_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    card = await svc.archive_card(
        db,
        workspace_id=ws_id,
        card_id=card_id,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="card.archived",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="board_card",
        resource_id=card.id,
        summary=f"archived card {card.title!r}",
        request=request,
    )
    await db.commit()
    return BoardCardRead.model_validate(card)


@router.post(
    "/cards/{card_id}/complete",
    response_model=BoardCardRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_write", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def complete_card(
    card_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BoardCardRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    card = await svc.complete_card(
        db,
        workspace_id=ws_id,
        card_id=card_id,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="card.completed",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="board_card",
        resource_id=card.id,
        summary=f"completed card {card.title!r}",
        request=request,
    )
    await db.commit()
    await db.refresh(card)
    return BoardCardRead.model_validate(card)


# ─── Agent inbox ────────────────────────────────────────────────
@router.get(
    "/agents/{agent_id}/cards",
    response_model=list[BoardCardRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("cards_per_agent", limit=60, period_seconds=60))],
    tags=["project_boards"],
)
async def list_cards_for_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[BoardCardRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_cards_for_agent(db, workspace_id=ws_id, agent_id=agent_id, limit=limit)
    return [BoardCardRead.model_validate(r) for r in rows]
