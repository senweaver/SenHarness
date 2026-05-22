"""Department repository — workspace-scoped tree."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.department import Department
from app.db.repository import AsyncRepository


class DepartmentRepository(AsyncRepository[Department]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Department)

    async def list_for_workspace(self, workspace_id: uuid.UUID) -> list[Department]:
        stmt = (
            select(Department)
            .where(Department.workspace_id == workspace_id)
            .where(Department.deleted_at.is_(None))
            .order_by(Department.path, Department.name)
        )
        return list((await self.session.execute(stmt)).scalars().all())
