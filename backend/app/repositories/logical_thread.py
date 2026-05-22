"""Repositories for cross-platform logical threads (M3.6)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.logical_thread import LogicalThread, ThreadChannelBinding
from app.db.repository import AsyncRepository


class LogicalThreadRepository(AsyncRepository[LogicalThread]):
    model = LogicalThread

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, LogicalThread)

    async def list_for_identity(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[LogicalThread]:
        stmt = (
            select(LogicalThread)
            .where(
                LogicalThread.workspace_id == workspace_id,
                LogicalThread.identity_id == identity_id,
                LogicalThread.deleted_at.is_(None),
            )
            .order_by(desc(LogicalThread.last_activity_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def find_active_for_identity_agent(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> LogicalThread | None:
        stmt = (
            select(LogicalThread)
            .where(
                LogicalThread.workspace_id == workspace_id,
                LogicalThread.identity_id == identity_id,
                LogicalThread.agent_id == agent_id,
                LogicalThread.deleted_at.is_(None),
            )
            .order_by(desc(LogicalThread.last_activity_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ThreadChannelBindingRepository(AsyncRepository[ThreadChannelBinding]):
    model = ThreadChannelBinding

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ThreadChannelBinding)

    async def get_by_channel_user(
        self,
        *,
        workspace_id: uuid.UUID,
        channel_id: uuid.UUID | None,
        external_user_id: str | None,
    ) -> ThreadChannelBinding | None:
        stmt = select(ThreadChannelBinding).where(
            ThreadChannelBinding.workspace_id == workspace_id,
            ThreadChannelBinding.deleted_at.is_(None),
        )
        if channel_id is None:
            stmt = stmt.where(ThreadChannelBinding.channel_id.is_(None))
        else:
            stmt = stmt.where(ThreadChannelBinding.channel_id == channel_id)
        if external_user_id is None:
            stmt = stmt.where(ThreadChannelBinding.external_user_id.is_(None))
        else:
            stmt = stmt.where(ThreadChannelBinding.external_user_id == external_user_id)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def list_for_thread(
        self,
        *,
        workspace_id: uuid.UUID,
        thread_id: uuid.UUID,
    ) -> Sequence[ThreadChannelBinding]:
        stmt = (
            select(ThreadChannelBinding)
            .where(
                ThreadChannelBinding.workspace_id == workspace_id,
                ThreadChannelBinding.thread_id == thread_id,
                ThreadChannelBinding.deleted_at.is_(None),
            )
            .order_by(desc(ThreadChannelBinding.last_seen_at))
        )
        return (await self.session.execute(stmt)).scalars().all()
