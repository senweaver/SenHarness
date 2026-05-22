"""Move + reorder tests for the kanban service (M4.4).

Covers:

* Reordering inside the same column.
* Cross-column moves (sort order packed in both source + target).
* Clamping of out-of-range ``target_position``.
* Sort-order density invariant (0..N-1) after every operation.
"""

from __future__ import annotations

import pytest

from app.db.models.board_card import BoardCardColumn
from app.repositories.project_board import BoardCardRepository
from app.services import project_board as svc

pytestmark = pytest.mark.asyncio


async def _build_board(db_session, workspace, identity):
    return await svc.create_board(
        db_session,
        workspace_id=workspace.id,
        name="board",
        description=None,
        squad_id=None,
        actor_identity_id=identity.id,
    )


async def _seed_column(db_session, workspace, identity, board, column, count: int):
    cards = []
    for i in range(count):
        c = await svc.create_card(
            db_session,
            workspace_id=workspace.id,
            board_id=board.id,
            title=f"{column.value}-{i}",
            column=column,
            actor_identity_id=identity.id,
        )
        cards.append(c)
    return cards


def _orders_in(rows):
    return [int(r.sort_order) for r in rows]


async def test_move_within_same_column_reorders_neighbours(db_session, workspace, identity):
    board = await _build_board(db_session, workspace, identity)
    cards = await _seed_column(db_session, workspace, identity, board, BoardCardColumn.BACKLOG, 3)
    assert _orders_in(cards) == [0, 1, 2]

    # Move the third card to the top of the same column.
    moved = await svc.move_card(
        db_session,
        workspace_id=workspace.id,
        card_id=cards[2].id,
        target_column=BoardCardColumn.BACKLOG,
        target_position=0,
        actor_identity_id=identity.id,
    )
    assert moved.column == BoardCardColumn.BACKLOG
    assert int(moved.sort_order) == 0

    fresh = list(
        await BoardCardRepository(db_session).list_cards_in_column(
            board_id=board.id, column=BoardCardColumn.BACKLOG
        )
    )
    titles_in_order = [c.title for c in fresh]
    assert titles_in_order == [
        "backlog-2",
        "backlog-0",
        "backlog-1",
    ]
    assert _orders_in(fresh) == [0, 1, 2]


async def test_move_to_other_column_packs_both_sides(db_session, workspace, identity):
    board = await _build_board(db_session, workspace, identity)
    backlog_cards = await _seed_column(
        db_session, workspace, identity, board, BoardCardColumn.BACKLOG, 3
    )
    review_cards = await _seed_column(
        db_session, workspace, identity, board, BoardCardColumn.REVIEW, 2
    )

    # Move backlog[1] into the middle (position=1) of review.
    moved = await svc.move_card(
        db_session,
        workspace_id=workspace.id,
        card_id=backlog_cards[1].id,
        target_column=BoardCardColumn.REVIEW,
        target_position=1,
        actor_identity_id=identity.id,
    )
    assert moved.column == BoardCardColumn.REVIEW

    # Source column should now have 2 dense cards (0, 1).
    repo = BoardCardRepository(db_session)
    fresh_backlog = list(
        await repo.list_cards_in_column(board_id=board.id, column=BoardCardColumn.BACKLOG)
    )
    assert [c.title for c in fresh_backlog] == ["backlog-0", "backlog-2"]
    assert _orders_in(fresh_backlog) == [0, 1]

    # Target column should now have 3 dense cards with the inserted
    # card at position 1.
    fresh_review = list(
        await repo.list_cards_in_column(board_id=board.id, column=BoardCardColumn.REVIEW)
    )
    assert [c.title for c in fresh_review] == [
        "review-0",
        "backlog-1",
        "review-1",
    ]
    assert _orders_in(fresh_review) == [0, 1, 2]
    _ = review_cards  # kept for fixture completeness


async def test_move_clamps_position_above_column_size(db_session, workspace, identity):
    board = await _build_board(db_session, workspace, identity)
    cards = await _seed_column(
        db_session, workspace, identity, board, BoardCardColumn.IN_PROGRESS, 2
    )

    moved = await svc.move_card(
        db_session,
        workspace_id=workspace.id,
        card_id=cards[0].id,
        target_column=BoardCardColumn.DONE,
        target_position=99,  # silently clamped
        actor_identity_id=identity.id,
    )
    assert moved.column == BoardCardColumn.DONE
    fresh_done = list(
        await BoardCardRepository(db_session).list_cards_in_column(
            board_id=board.id, column=BoardCardColumn.DONE
        )
    )
    assert [c.title for c in fresh_done] == ["in_progress-0"]
    assert _orders_in(fresh_done) == [0]


async def test_complete_card_pushes_to_bottom_of_done(db_session, workspace, identity):
    board = await _build_board(db_session, workspace, identity)
    done_existing = await _seed_column(
        db_session, workspace, identity, board, BoardCardColumn.DONE, 2
    )
    in_progress = await _seed_column(
        db_session, workspace, identity, board, BoardCardColumn.IN_PROGRESS, 1
    )

    completed = await svc.complete_card(
        db_session,
        workspace_id=workspace.id,
        card_id=in_progress[0].id,
        actor_identity_id=identity.id,
    )
    assert completed.column == BoardCardColumn.DONE
    fresh_done = list(
        await BoardCardRepository(db_session).list_cards_in_column(
            board_id=board.id, column=BoardCardColumn.DONE
        )
    )
    assert [c.title for c in fresh_done] == [
        "done-0",
        "done-1",
        "in_progress-0",
    ]
    assert _orders_in(fresh_done) == [0, 1, 2]
    _ = done_existing
