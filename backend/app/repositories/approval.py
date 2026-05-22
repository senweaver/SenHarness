"""Approval repository — CRUD + queries for HITL audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.department import Department
from app.db.models.membership import Membership
from app.db.repository import AsyncRepository


class ApprovalRepository(AsyncRepository[Approval]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Approval)

    async def list_pending(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[Approval]:
        stmt = (
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(desc(Approval.created_at))
            .limit(limit)
        )
        if session_id is not None:
            stmt = stmt.where(Approval.session_id == session_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_urgent_pending(
        self,
        *,
        workspace_id: uuid.UUID,
        limit: int = 5,
    ) -> list[Approval]:
        """Top-N pending approvals sorted by urgency (earliest expiry first).

        Rows without ``expires_at`` are pushed to the end since they can't be
        ranked. Used by the sidebar bell preview.
        """
        stmt = (
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .where(Approval.status == ApprovalStatus.PENDING)
            # NULLS LAST is Postgres-specific; we rely on that + asc for
            # deterministic ordering with soonest-expiring first.
            .order_by(asc(Approval.expires_at).nulls_last(), desc(Approval.created_at))
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def department_names_for_identities(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, str]:
        """Resolve ``identity_id -> department_name`` for a single workspace.

        Only returns entries where the membership has a department assigned;
        callers treat missing keys as "no department". One small query keeps
        approval-list rendering allocation-free.
        """
        if not identity_ids:
            return {}
        stmt = (
            select(Membership.identity_id, Department.name)
            .join(Department, Department.id == Membership.department_id)
            .where(Membership.workspace_id == workspace_id)
            .where(Membership.identity_id.in_(identity_ids))
        )
        rows = (await self.session.execute(stmt)).all()
        return dict(rows)

    async def list_recent(
        self,
        *,
        workspace_id: uuid.UUID,
        limit: int = 50,
    ) -> list[Approval]:
        stmt = (
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .order_by(desc(Approval.created_at))
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        tool_name: str,
        tool_args: dict[str, Any],
        summary: str | None,
        requested_by_identity_id: uuid.UUID | None,
        expires_at: datetime | None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> Approval:
        row = Approval(
            workspace_id=workspace_id,
            session_id=session_id,
            agent_id=agent_id,
            run_id=run_id,
            tool_name=tool_name,
            tool_args=tool_args,
            summary=summary,
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=requested_by_identity_id,
            expires_at=expires_at,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def decide(
        self,
        *,
        approval_id: uuid.UUID,
        workspace_id: uuid.UUID,
        approved: bool,
        reason: str | None,
        decided_by_identity_id: uuid.UUID | None,
        now: datetime,
        status_override: ApprovalStatus | None = None,
    ) -> Approval | None:
        """Mark a pending approval as decided.

        * ``status_override=EXPIRED`` lets the timeout path close out rows as
          *expired* instead of rolling them into *denied*, which keeps the
          audit feed honest.
        """
        stmt = (
            select(Approval)
            .where(Approval.id == approval_id)
            .where(Approval.workspace_id == workspace_id)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if row.status != ApprovalStatus.PENDING:
            return row
        if status_override is not None:
            row.status = status_override
        else:
            row.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        row.decided_at = now
        row.decided_reason = reason
        row.decided_by_identity_id = decided_by_identity_id
        await self.session.flush()
        return row

    async def count_pending(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> int:
        stmt = (
            select(func.count(Approval.id))
            .where(Approval.workspace_id == workspace_id)
            .where(Approval.status == ApprovalStatus.PENDING)
        )
        return int((await self.session.execute(stmt)).scalar() or 0)

    async def cancel_pending_for_run(
        self,
        *,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
        decided_by_identity_id: uuid.UUID | None,
        reason: str,
        now: datetime,
    ) -> list[Approval]:
        """Flip every pending approval tied to the given run to CANCELLED.

        Used by the external agent gateway when a client sends ``run.cancel``
        — the session_id isn't directly available over the wire, but run_id
        is guaranteed to be there.
        """
        stmt = (
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .where(Approval.status == ApprovalStatus.PENDING)
            .where(Approval.run_id == run_id)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            row.status = ApprovalStatus.CANCELLED
            row.decided_at = now
            row.decided_reason = reason
            row.decided_by_identity_id = decided_by_identity_id
        if rows:
            await self.session.flush()
        return rows

    async def cancel_pending_for_session(
        self,
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        run_id: uuid.UUID | None,
        decided_by_identity_id: uuid.UUID | None,
        reason: str,
        now: datetime,
    ) -> list[Approval]:
        """Bulk-mark a session's pending approvals as CANCELLED.

        Called when the user cancels a turn over the WS — leaving them in
        ``pending`` would keep the row blocking the runner callback and
        pollute the badge count. When ``run_id`` is provided we scope further
        to that specific run, which is preferable because concurrent runs
        shouldn't clobber each other.
        """
        stmt = (
            select(Approval)
            .where(Approval.workspace_id == workspace_id)
            .where(Approval.session_id == session_id)
            .where(Approval.status == ApprovalStatus.PENDING)
        )
        if run_id is not None:
            stmt = stmt.where(Approval.run_id == run_id)
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            row.status = ApprovalStatus.CANCELLED
            row.decided_at = now
            row.decided_reason = reason
            row.decided_by_identity_id = decided_by_identity_id
        if rows:
            await self.session.flush()
        return rows
