"""Repository helpers for :class:`SubAgentRun` (M2.5.1)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.db.repository import AsyncRepository


class SubAgentRunRepository(AsyncRepository[SubAgentRun]):
    model = SubAgentRun

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SubAgentRun)

    async def get_by_child_run_id(
        self, *, child_run_id: uuid.UUID
    ) -> SubAgentRun | None:
        stmt = (
            select(SubAgentRun)
            .where(SubAgentRun.child_run_id == child_run_id)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_parent_run_id(
        self,
        *,
        parent_run_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[SubAgentRun]:
        stmt = (
            select(SubAgentRun)
            .where(SubAgentRun.parent_run_id == parent_run_id)
            .order_by(asc(SubAgentRun.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_active(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> Sequence[SubAgentRun]:
        """Active = ``RUNNING`` or ``HALLUCINATION_REVIEW`` (admin gating)."""
        stmt = (
            select(SubAgentRun)
            .where(
                SubAgentRun.state.in_(
                    [
                        SubAgentRunState.RUNNING,
                        SubAgentRunState.HALLUCINATION_REVIEW,
                    ]
                )
            )
            .order_by(desc(SubAgentRun.last_heartbeat_at))
            .limit(limit)
        )
        if workspace_id is not None:
            stmt = stmt.where(SubAgentRun.workspace_id == workspace_id)
        if parent_run_id is not None:
            stmt = stmt.where(SubAgentRun.parent_run_id == parent_run_id)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_stale(
        self,
        *,
        cutoff: datetime,
        limit: int = 200,
    ) -> Sequence[SubAgentRun]:
        """Rows with ``state=RUNNING AND last_heartbeat_at < cutoff``.

        Backed by ``ix_subagent_runs_state_heartbeat`` so the reaper
        sweep is an index-only seek even on a large workspace.
        """
        stmt = (
            select(SubAgentRun)
            .where(SubAgentRun.state == SubAgentRunState.RUNNING)
            .where(SubAgentRun.last_heartbeat_at < cutoff)
            .order_by(asc(SubAgentRun.last_heartbeat_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
