"""M0.11 retention cascade — kanban tables propagate workspace soft-delete.

Verifies that when ``cascade_for_workspace`` runs against a workspace
that owns boards + cards, both tables are soft-deleted (``deleted_at``
stamped). The two ``CASCADE_TARGETS`` entries added in M4.4 are the
only thing this test exercises — every other table is covered by the
existing ``test_retention_cascade.py`` module.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.board_card import BoardCard, BoardCardColumn
from app.db.models.project_board import ProjectBoard
from app.services import project_board as svc
from app.services import retention as retention_svc

pytestmark = pytest.mark.asyncio


async def test_workspace_cascade_soft_deletes_boards_and_cards(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="will-be-cascaded",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="will-also-be-cascaded",
        column=BoardCardColumn.IN_PROGRESS,
        actor_identity_id=identity.id,
    )

    affected = await retention_svc.cascade_for_workspace(
        db_session, workspace_id=workspace.id
    )

    assert affected.get("project_boards", 0) >= 1
    assert affected.get("board_cards", 0) >= 1

    fresh_board = (
        await db_session.execute(
            select(ProjectBoard).where(ProjectBoard.id == board.id)
        )
    ).scalar_one()
    assert fresh_board.deleted_at is not None

    fresh_card = (
        await db_session.execute(
            select(BoardCard).where(BoardCard.id == card.id)
        )
    ).scalar_one()
    assert fresh_card.deleted_at is not None


async def test_cascade_is_idempotent(db_session, workspace, identity):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="board",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="card",
        actor_identity_id=identity.id,
    )

    first = await retention_svc.cascade_for_workspace(
        db_session, workspace_id=workspace.id
    )
    assert first.get("project_boards", 0) >= 1
    assert first.get("board_cards", 0) >= 1

    second = await retention_svc.cascade_for_workspace(
        db_session, workspace_id=workspace.id
    )
    assert second.get("project_boards", 0) == 0
    assert second.get("board_cards", 0) == 0


async def test_workspace_cascade_does_not_appear_in_identity_cascade(
    db_session, workspace, identity
):
    """``project_boards`` / ``board_cards`` are workspace-only (
    identity_scoped=False). Identity cascade must not include them.
    """
    await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="board",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    affected = await retention_svc.cascade_for_identity(
        db_session, identity_id=identity.id
    )
    assert "project_boards" not in affected
    assert "board_cards" not in affected
