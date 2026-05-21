"""Repository for per-event skill usage telemetry (M1.3)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.repository import AsyncRepository


class SkillUsageRepository(AsyncRepository[SkillUsage]):
    model = SkillUsage

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SkillUsage)

    async def record(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
        version_id: uuid.UUID | None,
        run_id: uuid.UUID,
        session_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        identity_id: uuid.UUID | None,
        event_kind: SkillUsageEventKind,
        contribution_score: float | None = None,
    ) -> SkillUsage:
        row = SkillUsage(
            workspace_id=workspace_id,
            pack_id=pack_id,
            version_id=version_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
            event_kind=event_kind,
            contribution_score=contribution_score,
        )
        self.session.add(row)
        await self.session.flush([row])
        return row

    async def list_for_pack(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
        limit: int = 200,
        since: datetime | None = None,
        event_kind: SkillUsageEventKind | None = None,
    ) -> Sequence[SkillUsage]:
        stmt = select(SkillUsage).where(
            SkillUsage.workspace_id == workspace_id,
            SkillUsage.pack_id == pack_id,
        )
        if since is not None:
            stmt = stmt.where(SkillUsage.created_at >= since)
        if event_kind is not None:
            stmt = stmt.where(SkillUsage.event_kind == event_kind)
        stmt = stmt.order_by(desc(SkillUsage.created_at)).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_run(
        self,
        *,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> Sequence[SkillUsage]:
        stmt = (
            select(SkillUsage)
            .where(
                SkillUsage.workspace_id == workspace_id,
                SkillUsage.run_id == run_id,
            )
            .order_by(SkillUsage.created_at.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def count_by_kind_since(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
        since: datetime,
        event_kind: SkillUsageEventKind | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(SkillUsage)
            .where(
                SkillUsage.workspace_id == workspace_id,
                SkillUsage.pack_id == pack_id,
                SkillUsage.created_at >= since,
            )
        )
        if event_kind is not None:
            stmt = stmt.where(SkillUsage.event_kind == event_kind)
        return int((await self.session.execute(stmt)).scalar() or 0)

    async def aggregate_pack_stats(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
        since: datetime,
    ) -> dict[str, Any]:
        """Return ``{use_count, last_used_at, contribution_avg, by_kind}``.

        ``contribution_avg`` is computed only over rows where the
        ``contribution_score`` column is non-null so an empty score
        cohort never depresses the rolling average. ``by_kind`` is a
        ``{kind: count}`` map for every kind that fired at least once
        in the window — kinds with zero hits are intentionally absent.
        """
        agg_stmt = select(
            func.count().label("use_count"),
            func.max(SkillUsage.created_at).label("last_used_at"),
            func.avg(SkillUsage.contribution_score).label("contribution_avg"),
        ).where(
            SkillUsage.workspace_id == workspace_id,
            SkillUsage.pack_id == pack_id,
            SkillUsage.created_at >= since,
        )
        agg = (await self.session.execute(agg_stmt)).one()

        by_kind_stmt = (
            select(SkillUsage.event_kind, func.count().label("c"))
            .where(
                SkillUsage.workspace_id == workspace_id,
                SkillUsage.pack_id == pack_id,
                SkillUsage.created_at >= since,
            )
            .group_by(SkillUsage.event_kind)
        )
        by_kind_rows = (await self.session.execute(by_kind_stmt)).all()
        by_kind: dict[str, int] = {}
        for kind, count in by_kind_rows:
            key = kind.value if isinstance(kind, SkillUsageEventKind) else str(kind)
            by_kind[key] = int(count)

        return {
            "use_count": int(agg.use_count or 0),
            "last_used_at": agg.last_used_at,
            "contribution_avg": (
                float(agg.contribution_avg) if agg.contribution_avg is not None else None
            ),
            "by_kind": by_kind,
        }
