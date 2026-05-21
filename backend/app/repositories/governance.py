"""Repositories for governance entities."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, or_, select

from app.db.models.governance import Budget, Policy, ToolCallLog, UsageEvent
from app.db.repository import AsyncRepository


class PolicyRepository(AsyncRepository[Policy]):
    model = Policy

    async def list_visible_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        offset: int = 0,
        limit: int = 200,
    ) -> Sequence[Policy]:
        stmt = (
            select(Policy)
            .where(
                Policy.deleted_at.is_(None),
                or_(Policy.workspace_id == workspace_id, Policy.scope == "global"),
            )
            .order_by(desc(Policy.priority), desc(Policy.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class BudgetRepository(AsyncRepository[Budget]):
    model = Budget

    async def list_visible_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        offset: int = 0,
        limit: int = 200,
    ) -> Sequence[Budget]:
        stmt = (
            select(Budget)
            .where(
                Budget.deleted_at.is_(None),
                or_(Budget.workspace_id == workspace_id, Budget.scope == "global"),
            )
            .order_by(desc(Budget.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class UsageEventRepository(AsyncRepository[UsageEvent]):
    model = UsageEvent

    async def list_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        offset: int = 0,
        limit: int = 200,
    ) -> Sequence[UsageEvent]:
        stmt = (
            select(UsageEvent)
            .where(UsageEvent.workspace_id == workspace_id)
            .order_by(desc(UsageEvent.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class ToolCallLogRepository(AsyncRepository[ToolCallLog]):
    model = ToolCallLog

    async def list_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        offset: int = 0,
        limit: int = 200,
    ) -> Sequence[ToolCallLog]:
        stmt = (
            select(ToolCallLog)
            .where(ToolCallLog.workspace_id == workspace_id)
            .order_by(desc(ToolCallLog.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
