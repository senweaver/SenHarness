"""Repository for the pending-memory queue (M0.7).

Wraps the generic :class:`AsyncRepository` with three query patterns
the service layer reaches for repeatedly:

* per-session drain — used by the post-FINAL promote hook;
* per-workspace sweep — used by the ARQ backstop cron;
* per-status terminal mutations — promote / skip / fail.

All queries are workspace-scoped at the SQL level. Cross-tenant
reads must route through the platform-admin surface, not this repo.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import asc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.pending_memory import (
    PendingMemory,
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.db.repository import AsyncRepository


class PendingMemoryRepository(AsyncRepository[PendingMemory]):
    model = PendingMemory

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PendingMemory)

    async def list_pending_for_session(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        target_table: PendingMemoryTargetTable | None = None,
        limit: int = 200,
    ) -> Sequence[PendingMemory]:
        """Drain order: oldest first so promotion preserves intent ordering."""
        stmt = (
            select(PendingMemory)
            .where(
                PendingMemory.workspace_id == workspace_id,
                PendingMemory.session_id == session_id,
                PendingMemory.status == PendingMemoryStatus.PENDING,
                PendingMemory.deleted_at.is_(None),
            )
            .order_by(asc(PendingMemory.created_at))
            .limit(limit)
        )
        if target_table is not None:
            stmt = stmt.where(PendingMemory.target_table == target_table)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_session(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[PendingMemory]:
        """Return all rows for a session regardless of status — drives the
        per-session UI drawer where the user wants to see promoted /
        skipped lineage too.
        """
        stmt = (
            select(PendingMemory)
            .where(
                PendingMemory.workspace_id == workspace_id,
                PendingMemory.session_id == session_id,
                PendingMemory.deleted_at.is_(None),
            )
            .order_by(asc(PendingMemory.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_pending_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        status: PendingMemoryStatus = PendingMemoryStatus.PENDING,
        older_than: datetime | None = None,
        limit: int = 200,
    ) -> Sequence[PendingMemory]:
        stmt = (
            select(PendingMemory)
            .where(
                PendingMemory.workspace_id == workspace_id,
                PendingMemory.status == status,
                PendingMemory.deleted_at.is_(None),
            )
            .order_by(asc(PendingMemory.created_at))
            .limit(limit)
        )
        if older_than is not None:
            stmt = stmt.where(PendingMemory.created_at < older_than)
        return (await self.session.execute(stmt)).scalars().all()

    async def workspace_status_counts(
        self, *, workspace_id: uuid.UUID
    ) -> dict[str, int]:
        """``{status: count}`` for the admin stats card. Soft-deleted
        rows are excluded so the dashboard tracks live queue depth.
        """
        stmt = (
            select(PendingMemory.status, func.count())
            .where(
                PendingMemory.workspace_id == workspace_id,
                PendingMemory.deleted_at.is_(None),
            )
            .group_by(PendingMemory.status)
        )
        rows = (await self.session.execute(stmt)).all()
        out = {s.value: 0 for s in PendingMemoryStatus}
        for raw_status, count in rows:
            key = (
                raw_status.value
                if hasattr(raw_status, "value")
                else str(raw_status)
            )
            out[key] = int(count)
        return out

    async def workspace_oldest_pending(
        self, *, workspace_id: uuid.UUID
    ) -> datetime | None:
        stmt = select(func.min(PendingMemory.created_at)).where(
            PendingMemory.workspace_id == workspace_id,
            PendingMemory.status == PendingMemoryStatus.PENDING,
            PendingMemory.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_workspace_ids(
        self, *, since: datetime | None = None, limit: int = 500
    ) -> Sequence[uuid.UUID]:
        """All workspaces with at least one PENDING row.

        Drives the cron sweep — no point visiting a workspace that has
        nothing in flight. ``since`` filters by row ``created_at`` so a
        steady-state workspace doesn't burn a worker tick when its
        backlog has already been drained.
        """
        stmt = (
            select(PendingMemory.workspace_id)
            .where(
                PendingMemory.status == PendingMemoryStatus.PENDING,
                PendingMemory.deleted_at.is_(None),
            )
            .group_by(PendingMemory.workspace_id)
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(PendingMemory.created_at >= since)
        return [row for row in (await self.session.execute(stmt)).scalars().all()]

    async def mark_promoted(
        self,
        *,
        pending: PendingMemory,
        target_id: uuid.UUID | None,
    ) -> PendingMemory:
        pending.status = PendingMemoryStatus.PROMOTED
        pending.promoted_at = utcnow_naive()
        pending.promoted_target_id = target_id
        pending.failure_reason = None
        await self.session.flush([pending])
        await self.session.refresh(pending)
        return pending

    async def mark_skipped(
        self, *, pending: PendingMemory, reason: str
    ) -> PendingMemory:
        pending.status = PendingMemoryStatus.SKIPPED
        pending.failure_reason = reason[:200]
        await self.session.flush([pending])
        await self.session.refresh(pending)
        return pending

    async def mark_failed(
        self, *, pending: PendingMemory, reason: str
    ) -> PendingMemory:
        pending.status = PendingMemoryStatus.FAILED
        pending.failure_reason = reason[:200]
        pending.failure_count = int(pending.failure_count or 0) + 1
        await self.session.flush([pending])
        await self.session.refresh(pending)
        return pending

    async def reset_failed_to_pending(
        self, *, pending: PendingMemory
    ) -> PendingMemory:
        """Flip a transient ``FAILED`` row back into the ``PENDING`` queue
        so the next promote pass picks it up again. ``failure_count`` is
        intentionally preserved so the ceiling check still kicks in.
        """
        pending.status = PendingMemoryStatus.PENDING
        await self.session.flush([pending])
        await self.session.refresh(pending)
        return pending

    async def list_eligible_for_retry(
        self,
        *,
        workspace_id: uuid.UUID,
        max_failure_count: int,
        older_than: datetime | None = None,
        limit: int = 200,
    ) -> Sequence[PendingMemory]:
        """``FAILED`` rows that haven't blown the retry budget yet."""
        stmt = (
            select(PendingMemory)
            .where(
                PendingMemory.workspace_id == workspace_id,
                PendingMemory.status == PendingMemoryStatus.FAILED,
                PendingMemory.failure_count < max_failure_count,
                PendingMemory.deleted_at.is_(None),
            )
            .order_by(asc(PendingMemory.created_at))
            .limit(limit)
        )
        if older_than is not None:
            stmt = stmt.where(PendingMemory.created_at < older_than)
        return (await self.session.execute(stmt)).scalars().all()


def cutoff_age(seconds: int) -> datetime:
    """Helper: ``utcnow() - seconds`` in the project's naive-UTC convention."""
    return utcnow_naive() - timedelta(seconds=int(seconds))
