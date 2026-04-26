"""Flow + FlowRun repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select

from app.db.models.flow import Flow, FlowRun
from app.db.repository import AsyncRepository


class FlowRepository(AsyncRepository[Flow]):
    model = Flow

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[Flow]:
        stmt = (
            select(Flow)
            .where(
                Flow.workspace_id == workspace_id,
                Flow.deleted_at.is_(None),
            )
            .order_by(desc(Flow.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_enabled_cron_flows(self) -> Sequence[Flow]:
        """All enabled flows with ``trigger_kind=cron``. Used by the scheduler."""
        stmt = select(Flow).where(
            Flow.deleted_at.is_(None),
            Flow.enabled.is_(True),
            Flow.trigger_kind == "cron",
        )
        return (await self.session.execute(stmt)).scalars().all()


class FlowRunRepository(AsyncRepository[FlowRun]):
    model = FlowRun

    async def list_for_flow(
        self, *, flow_id: uuid.UUID, limit: int = 50
    ) -> Sequence[FlowRun]:
        stmt = (
            select(FlowRun)
            .where(FlowRun.flow_id == flow_id)
            .order_by(desc(FlowRun.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
