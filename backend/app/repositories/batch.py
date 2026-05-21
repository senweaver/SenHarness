"""Repositories for session checkpoints + batch replay."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import asc, desc, select

from app.db.models.batch import BatchRun, BatchRunCase
from app.db.models.checkpoint import SessionCheckpoint
from app.db.repository import AsyncRepository


class SessionCheckpointRepository(AsyncRepository[SessionCheckpoint]):
    model = SessionCheckpoint

    async def list_for_session(
        self, *, session_id: uuid.UUID, limit: int = 100
    ) -> Sequence[SessionCheckpoint]:
        stmt = (
            select(SessionCheckpoint)
            .where(SessionCheckpoint.session_id == session_id)
            .order_by(desc(SessionCheckpoint.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class BatchRunRepository(AsyncRepository[BatchRun]):
    model = BatchRun

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 50
    ) -> Sequence[BatchRun]:
        stmt = (
            select(BatchRun)
            .where(BatchRun.workspace_id == workspace_id)
            .order_by(desc(BatchRun.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class BatchRunCaseRepository(AsyncRepository[BatchRunCase]):
    model = BatchRunCase

    async def list_for_run(
        self, *, batch_run_id: uuid.UUID
    ) -> Sequence[BatchRunCase]:
        stmt = (
            select(BatchRunCase)
            .where(BatchRunCase.batch_run_id == batch_run_id)
            .order_by(asc(BatchRunCase.created_at))
        )
        return (await self.session.execute(stmt)).scalars().all()
