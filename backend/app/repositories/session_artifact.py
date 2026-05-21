"""Repository for the captured per-run artifact (M0.2)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session_artifact import SessionArtifact
from app.db.repository import AsyncRepository


class SessionArtifactRepository(AsyncRepository[SessionArtifact]):
    model = SessionArtifact

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SessionArtifact)

    async def get_by_run_id(
        self, *, workspace_id: uuid.UUID, run_id: uuid.UUID
    ) -> SessionArtifact | None:
        stmt = (
            select(SessionArtifact)
            .where(
                SessionArtifact.workspace_id == workspace_id,
                SessionArtifact.run_id == run_id,
                SessionArtifact.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_session(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[SessionArtifact]:
        stmt = (
            select(SessionArtifact)
            .where(
                SessionArtifact.workspace_id == workspace_id,
                SessionArtifact.session_id == session_id,
                SessionArtifact.deleted_at.is_(None),
            )
            .order_by(desc(SessionArtifact.finished_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_recent_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        since: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[SessionArtifact]:
        stmt = (
            select(SessionArtifact)
            .where(
                SessionArtifact.workspace_id == workspace_id,
                SessionArtifact.deleted_at.is_(None),
            )
            .order_by(desc(SessionArtifact.finished_at))
            .offset(offset)
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(SessionArtifact.finished_at >= since)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_unjudged(
        self,
        *,
        workspace_id: uuid.UUID,
        limit: int = 100,
    ) -> Sequence[SessionArtifact]:
        """Artifacts the M0.3 judge hasn't scored yet, oldest first.

        Oldest-first ordering keeps the judge backlog FIFO so a sudden
        burst of new runs can't starve older artifacts indefinitely.
        """
        stmt = (
            select(SessionArtifact)
            .where(
                SessionArtifact.workspace_id == workspace_id,
                SessionArtifact.deleted_at.is_(None),
                SessionArtifact.judge_score.is_(None),
            )
            .order_by(asc(SessionArtifact.finished_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
