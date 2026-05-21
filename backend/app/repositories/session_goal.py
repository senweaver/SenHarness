"""Repositories for the session-goal lock + alignment-score tables (M0.1)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session_goal import GoalAlignmentScore, SessionGoal
from app.db.repository import AsyncRepository


class SessionGoalRepository(AsyncRepository[SessionGoal]):
    model = SessionGoal

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SessionGoal)

    async def get_active(
        self, *, session_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> SessionGoal | None:
        """Return the currently locked goal (``unlocked_at IS NULL``) or None.

        We tolerate concurrent locks during the brief window an operator
        might be replacing a stale goal: the *most recent* still-unlocked
        row wins.
        """
        stmt = (
            select(SessionGoal)
            .where(
                SessionGoal.session_id == session_id,
                SessionGoal.workspace_id == workspace_id,
                SessionGoal.deleted_at.is_(None),
                SessionGoal.unlocked_at.is_(None),
            )
            .order_by(desc(SessionGoal.locked_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_session(
        self,
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        include_unlocked: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[SessionGoal]:
        stmt = (
            select(SessionGoal)
            .where(
                SessionGoal.session_id == session_id,
                SessionGoal.workspace_id == workspace_id,
                SessionGoal.deleted_at.is_(None),
            )
            .order_by(desc(SessionGoal.locked_at))
            .offset(offset)
            .limit(limit)
        )
        if not include_unlocked:
            stmt = stmt.where(SessionGoal.unlocked_at.is_(None))
        return (await self.session.execute(stmt)).scalars().all()


class GoalAlignmentScoreRepository(AsyncRepository[GoalAlignmentScore]):
    model = GoalAlignmentScore

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, GoalAlignmentScore)

    async def list_for_session(
        self,
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[GoalAlignmentScore]:
        # Joins through session_goals so we never expose scores from
        # another workspace via a stale goal id.
        stmt = (
            select(GoalAlignmentScore)
            .join(
                SessionGoal,
                SessionGoal.id == GoalAlignmentScore.session_goal_id,
            )
            .where(
                SessionGoal.session_id == session_id,
                SessionGoal.workspace_id == workspace_id,
                GoalAlignmentScore.workspace_id == workspace_id,
            )
            .order_by(asc(GoalAlignmentScore.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_for_message(
        self,
        *,
        session_goal_id: uuid.UUID,
        message_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> GoalAlignmentScore | None:
        stmt = (
            select(GoalAlignmentScore)
            .where(
                GoalAlignmentScore.session_goal_id == session_goal_id,
                GoalAlignmentScore.message_id == message_id,
                GoalAlignmentScore.workspace_id == workspace_id,
            )
            .order_by(desc(GoalAlignmentScore.created_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
