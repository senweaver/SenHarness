"""Repository for the M3.4 per-agent profile."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent_profile import AgentProfile
from app.db.repository import AsyncRepository


class AgentProfileRepository(AsyncRepository[AgentProfile]):
    model = AgentProfile

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AgentProfile)

    async def get_by_agent(
        self, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> AgentProfile | None:
        stmt = (
            select(AgentProfile)
            .where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.agent_id == agent_id,
                AgentProfile.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_agent_any_workspace(self, *, agent_id: uuid.UUID) -> AgentProfile | None:
        """Lookup that ignores the workspace filter.

        Reserved for the platform-admin cross-workspace read path;
        regular workspace flows must always use :meth:`get_by_agent`.
        """
        stmt = (
            select(AgentProfile)
            .where(
                AgentProfile.agent_id == agent_id,
                AgentProfile.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[AgentProfile]:
        stmt = (
            select(AgentProfile)
            .where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.deleted_at.is_(None),
            )
            .order_by(desc(AgentProfile.last_aggregated_at))
            .limit(int(limit))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_all_platform(self, *, limit: int = 500) -> Sequence[AgentProfile]:
        """Cross-workspace listing — platform admin only at call site."""
        stmt = (
            select(AgentProfile)
            .where(AgentProfile.deleted_at.is_(None))
            .order_by(desc(AgentProfile.last_aggregated_at))
            .limit(int(limit))
        )
        return (await self.session.execute(stmt)).scalars().all()
