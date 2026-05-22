"""Repository helpers for :class:`InflightRun` (M2.5.2)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.repository import AsyncRepository


class InflightRunRepository(AsyncRepository[InflightRun]):
    model = InflightRun

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InflightRun)

    async def get_by_run_id(self, *, run_id: uuid.UUID) -> InflightRun | None:
        stmt = select(InflightRun).where(InflightRun.run_id == run_id).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_running(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        limit: int = 500,
    ) -> Sequence[InflightRun]:
        """Every row currently marked ``RUNNING``.

        Used by the startup recovery sweep — caller filters out rows
        whose ``pid_token`` matches the running process to avoid
        cancelling its own work.
        """
        stmt = (
            select(InflightRun)
            .where(InflightRun.state == InflightRunState.RUNNING)
            .order_by(asc(InflightRun.last_seen_at))
            .limit(limit)
        )
        if workspace_id is not None:
            stmt = stmt.where(InflightRun.workspace_id == workspace_id)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_stale_running(
        self,
        *,
        cutoff: datetime,
        limit: int = 200,
    ) -> Sequence[InflightRun]:
        """``state=RUNNING AND last_seen_at < cutoff``.

        Backed by ``ix_inflight_runs_state_last_seen_at`` so the cron
        sweep is an index-only seek.
        """
        stmt = (
            select(InflightRun)
            .where(InflightRun.state == InflightRunState.RUNNING)
            .where(InflightRun.last_seen_at < cutoff)
            .order_by(asc(InflightRun.last_seen_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_lost_for_session(
        self,
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        limit: int = 20,
    ) -> Sequence[InflightRun]:
        """Most-recent ``LOST`` rows for one session — used by the WS
        reconnect handshake to surface "your previous run was killed"."""
        stmt = (
            select(InflightRun)
            .where(InflightRun.session_id == session_id)
            .where(InflightRun.workspace_id == workspace_id)
            .where(InflightRun.state == InflightRunState.LOST)
            .order_by(desc(InflightRun.finished_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_active_for_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        limit: int = 200,
    ) -> Sequence[InflightRun]:
        """Active spine rows for the Agent View snapshot.

        Includes ``RUNNING`` and ``PAUSED`` — the live buckets the
        Agent View renders as cards. Terminal rows are filtered out by
        the snapshot service depending on the requested filter.
        """
        stmt = (
            select(InflightRun)
            .where(InflightRun.workspace_id == workspace_id)
            .where(InflightRun.state.in_((InflightRunState.RUNNING, InflightRunState.PAUSED)))
            .order_by(asc(InflightRun.started_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_active_for_workspaces(
        self,
        *,
        workspace_ids: Iterable[uuid.UUID],
        limit_per_workspace: int = 200,
    ) -> Sequence[InflightRun]:
        """Active spine rows across a set of workspaces in one query.

        Used by the workspace switcher summary endpoint to avoid an
        N+1 fan-out. ``limit_per_workspace`` is a soft per-workspace
        cap that the caller can use to bound the join; the SQL is a
        single SELECT bounded by ``limit_per_workspace * |workspaces|``
        which is good enough at the small workspace counts we cap to.
        """
        ids = list(workspace_ids)
        if not ids:
            return []
        stmt = (
            select(InflightRun)
            .where(InflightRun.workspace_id.in_(ids))
            .where(InflightRun.state.in_((InflightRunState.RUNNING, InflightRunState.PAUSED)))
            .order_by(asc(InflightRun.started_at))
            .limit(limit_per_workspace * max(len(ids), 1))
        )
        return (await self.session.execute(stmt)).scalars().all()
