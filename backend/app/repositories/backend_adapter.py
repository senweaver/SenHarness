"""BackendAdapter repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select

from app.db.models.backend_adapter import BackendAdapter
from app.db.repository import AsyncRepository


class BackendAdapterRepository(AsyncRepository[BackendAdapter]):
    model = BackendAdapter

    async def list_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        include_disabled: bool = True,
        limit: int = 200,
    ) -> Sequence[BackendAdapter]:
        stmt = (
            select(BackendAdapter)
            .where(
                BackendAdapter.workspace_id == workspace_id,
                BackendAdapter.deleted_at.is_(None),
            )
            .order_by(desc(BackendAdapter.created_at))
            .limit(limit)
        )
        if not include_disabled:
            stmt = stmt.where(BackendAdapter.enabled.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def find_by_api_key_hash(self, api_key_hash: str) -> BackendAdapter | None:
        """Hot-path auth lookup. Only returns non-deleted + enabled rows."""

        stmt = select(BackendAdapter).where(
            BackendAdapter.api_key_hash == api_key_hash,
            BackendAdapter.deleted_at.is_(None),
            BackendAdapter.enabled.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
