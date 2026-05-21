"""Service-layer lifecycle tests for the project kanban (M4.4).

Covers create / update / archive of boards plus create / update /
archive / complete of cards. Move logic has its own dedicated module.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.board_card import BoardCardColumn, BoardCardPriority
from app.services import project_board as svc

pytestmark = pytest.mark.asyncio


async def test_create_board_persists_row(db_session, workspace, identity):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="Sprint 42",
        description="Q4 ship list",
        squad_id=None,
        actor_identity_id=identity.id,
    )
    assert board.id is not None
    assert board.workspace_id == workspace.id
    assert board.name == "Sprint 42"
    assert board.description == "Q4 ship list"
    assert board.squad_id is None
    assert board.created_by == identity.id


async def test_create_board_rejects_duplicate_name(db_session, workspace, identity):
    await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="Inbox",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    with pytest.raises(Conflict) as exc:
        await svc.create_board(
            db_session,
            workspace_id=workspace.id,
            name="Inbox",
            description=None,
            squad_id=None,
            actor_identity_id=identity.id,
        )
    assert exc.value.code == "board.name_taken"


async def test_create_board_requires_non_empty_name(
    db_session, workspace, identity
):
    with pytest.raises(ValidationFailed):
        await svc.create_board(
            db_session,
            workspace_id=workspace.id,
            name="   ",
            description=None,
            squad_id=None,
            actor_identity_id=identity.id,
        )


async def test_update_board_changes_name_and_description(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="Old name",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    updated = await svc.update_board(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        name="New name",
        description="now with notes",
    )
    assert updated.name == "New name"
    assert updated.description == "now with notes"


async def test_update_board_blocks_name_collision(
    db_session, workspace, identity
):
    a = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="A",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="B",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    with pytest.raises(Conflict):
        await svc.update_board(
            db_session,
            workspace_id=workspace.id,
            board_id=a.id,
            name="B",
        )


async def test_archive_board_marks_deleted(db_session, workspace, identity):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="Doomed",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    archived = await svc.archive_board(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        actor_identity_id=identity.id,
    )
    assert archived.deleted_at is not None
    # Subsequent get_board call should 404 because the row is soft-deleted.
    with pytest.raises(NotFound):
        await svc.get_board(
            db_session, workspace_id=workspace.id, board_id=board.id
        )


async def test_create_card_lands_in_correct_column(
    db_session, workspace, identity, agent
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="Wire feature",
        description="rough plan",
        column=BoardCardColumn.IN_PROGRESS,
        priority=BoardCardPriority.HIGH,
        assignee_agent_id=agent.id,
        assignee_identity_id=identity.id,
        actor_identity_id=identity.id,
    )
    assert card.column == BoardCardColumn.IN_PROGRESS
    assert card.priority == BoardCardPriority.HIGH
    assert card.assignee_agent_id == agent.id
    assert card.assignee_identity_id == identity.id
    assert card.sort_order == 0


async def test_create_card_appends_with_sort_order(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    first = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="first",
        actor_identity_id=identity.id,
    )
    second = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="second",
        actor_identity_id=identity.id,
    )
    third = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="third",
        actor_identity_id=identity.id,
    )
    assert first.sort_order == 0
    assert second.sort_order == 1
    assert third.sort_order == 2


async def test_update_card_changes_priority_and_due(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="t",
        actor_identity_id=identity.id,
    )
    from datetime import datetime, timedelta

    new_due = datetime.utcnow() + timedelta(days=2)
    updated = await svc.update_card(
        db_session,
        workspace_id=workspace.id,
        card_id=card.id,
        priority=BoardCardPriority.URGENT,
        due_at=new_due,
    )
    assert updated.priority == BoardCardPriority.URGENT
    assert updated.due_at is not None


async def test_archive_card_soft_deletes(db_session, workspace, identity):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="t",
        actor_identity_id=identity.id,
    )
    await svc.archive_card(
        db_session,
        workspace_id=workspace.id,
        card_id=card.id,
        actor_identity_id=identity.id,
    )
    with pytest.raises(NotFound):
        await svc.get_card(
            db_session, workspace_id=workspace.id, card_id=card.id
        )


async def test_complete_card_moves_to_done_and_stamps_completed_at(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="t",
        column=BoardCardColumn.IN_PROGRESS,
        actor_identity_id=identity.id,
    )
    completed = await svc.complete_card(
        db_session,
        workspace_id=workspace.id,
        card_id=card.id,
        actor_identity_id=identity.id,
    )
    assert completed.column == BoardCardColumn.DONE
    assert completed.completed_at is not None


async def test_complete_card_is_idempotent(db_session, workspace, identity):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="t",
        actor_identity_id=identity.id,
    )
    first = await svc.complete_card(
        db_session,
        workspace_id=workspace.id,
        card_id=card.id,
        actor_identity_id=identity.id,
    )
    second = await svc.complete_card(
        db_session,
        workspace_id=workspace.id,
        card_id=card.id,
        actor_identity_id=identity.id,
    )
    assert first.completed_at == second.completed_at


async def test_create_card_rejects_cross_workspace_agent(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    rogue_agent_id = uuid.uuid4()
    with pytest.raises(NotFound):
        await svc.create_card(
            db_session,
            workspace_id=workspace.id,
            board_id=board.id,
            title="t",
            assignee_agent_id=rogue_agent_id,
            actor_identity_id=identity.id,
        )


async def test_get_board_rejects_cross_workspace_caller(
    db_session, workspace, identity
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    other_ws_id = uuid.uuid4()
    with pytest.raises(NotFound):
        await svc.get_board(
            db_session, workspace_id=other_ws_id, board_id=board.id
        )


async def test_list_cards_for_agent_skips_done(
    db_session, workspace, identity, agent
):
    board = await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="b",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )
    open_card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="open",
        column=BoardCardColumn.IN_PROGRESS,
        assignee_agent_id=agent.id,
        actor_identity_id=identity.id,
    )
    done_card = await svc.create_card(
        db_session,
        workspace_id=workspace.id,
        board_id=board.id,
        title="done",
        column=BoardCardColumn.DONE,
        assignee_agent_id=agent.id,
        actor_identity_id=identity.id,
    )
    rows = await svc.list_cards_for_agent(
        db_session, workspace_id=workspace.id, agent_id=agent.id
    )
    ids = [r.id for r in rows]
    assert open_card.id in ids
    assert done_card.id not in ids
