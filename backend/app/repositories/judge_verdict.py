"""Repository for the per-artifact M0.3 judge verdict."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.judge_verdict import JudgeVerdict
from app.db.repository import AsyncRepository


class JudgeVerdictRepository(AsyncRepository[JudgeVerdict]):
    model = JudgeVerdict

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, JudgeVerdict)

    async def get_by_artifact(
        self, *, workspace_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> JudgeVerdict | None:
        stmt = (
            select(JudgeVerdict)
            .where(
                JudgeVerdict.workspace_id == workspace_id,
                JudgeVerdict.artifact_id == artifact_id,
                JudgeVerdict.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_artifacts(
        self,
        *,
        workspace_id: uuid.UUID,
        artifact_ids: Sequence[uuid.UUID],
    ) -> Sequence[JudgeVerdict]:
        if not artifact_ids:
            return []
        stmt = select(JudgeVerdict).where(
            JudgeVerdict.workspace_id == workspace_id,
            JudgeVerdict.artifact_id.in_(list(artifact_ids)),
            JudgeVerdict.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def upsert_for_artifact(
        self,
        *,
        workspace_id: uuid.UUID,
        artifact_id: uuid.UUID,
        values: dict[str, Any],
    ) -> JudgeVerdict:
        """Insert or update the single verdict row for ``artifact_id``.

        We rely on the ``unique(artifact_id)`` index plus the ORM
        ``get → update or create`` two-step instead of an
        ``ON CONFLICT`` clause to keep the path portable across
        Postgres / SQLite test envs.
        """
        existing = await self.get_by_artifact(workspace_id=workspace_id, artifact_id=artifact_id)
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            await self.session.flush([existing])
            return existing
        return await self.create(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            **values,
        )

    async def delete_for_artifact(self, *, workspace_id: uuid.UUID, artifact_id: uuid.UUID) -> bool:
        existing = await self.get_by_artifact(workspace_id=workspace_id, artifact_id=artifact_id)
        if existing is None:
            return False
        await self.hard_delete(existing)
        return True
