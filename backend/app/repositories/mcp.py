"""Repositories for MCP servers and toolboxes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select

from app.db.models.mcp import McpServer, ToolBinding, Toolbox
from app.db.repository import AsyncRepository


class McpServerRepository(AsyncRepository[McpServer]):
    model = McpServer

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[McpServer]:
        stmt = (
            select(McpServer)
            .where(McpServer.workspace_id == workspace_id, McpServer.deleted_at.is_(None))
            .order_by(desc(McpServer.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class ToolboxRepository(AsyncRepository[Toolbox]):
    model = Toolbox

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[Toolbox]:
        stmt = (
            select(Toolbox)
            .where(Toolbox.workspace_id == workspace_id, Toolbox.deleted_at.is_(None))
            .order_by(desc(Toolbox.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class ToolBindingRepository(AsyncRepository[ToolBinding]):
    model = ToolBinding

    async def list_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        toolbox_id: uuid.UUID | None = None,
        limit: int = 500,
    ) -> Sequence[ToolBinding]:
        stmt = (
            select(ToolBinding)
            .where(ToolBinding.workspace_id == workspace_id, ToolBinding.deleted_at.is_(None))
            .order_by(desc(ToolBinding.priority), desc(ToolBinding.created_at))
            .limit(limit)
        )
        if toolbox_id is not None:
            stmt = stmt.where(ToolBinding.toolbox_id == toolbox_id)
        return (await self.session.execute(stmt)).scalars().all()
