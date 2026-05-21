"""Audit event + report repositories."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.agent_report import AgentReport, ReportStatus
from app.db.models.audit import AuditEvent
from app.db.models.identity import Identity


class AuditRepository:
    """Audit event queries + insertion."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        workspace_id: uuid.UUID | None,
        actor_identity_id: uuid.UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        summary: str | None = None,
        metadata_json: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditEvent:
        row = AuditEvent(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            metadata_json=metadata_json or {},
            ip_address=ip_address,
            user_agent=(user_agent or "")[:512] or None,
        )
        self.session.add(row)
        await self.session.flush([row])
        return row

    async def search(
        self,
        *,
        workspace_id: uuid.UUID | None,
        since: datetime | None = None,
        until: datetime | None = None,
        action: str | None = None,
        actor_identity_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[AuditEvent, Identity | None]]:
        conds = []
        if workspace_id is not None:
            conds.append(AuditEvent.workspace_id == workspace_id)
        if since is not None:
            conds.append(AuditEvent.created_at >= since)
        if until is not None:
            conds.append(AuditEvent.created_at < until)
        if action:
            if action.endswith("*"):
                conds.append(AuditEvent.action.like(action.rstrip("*") + "%"))
            else:
                conds.append(AuditEvent.action == action)
        if actor_identity_id is not None:
            conds.append(AuditEvent.actor_identity_id == actor_identity_id)
        if resource_type:
            conds.append(AuditEvent.resource_type == resource_type)
        if resource_id is not None:
            conds.append(AuditEvent.resource_id == resource_id)
        if q:
            like = f"%{q.strip()}%"
            conds.append(AuditEvent.summary.ilike(like))

        stmt = (
            select(AuditEvent, Identity)
            .outerjoin(Identity, Identity.id == AuditEvent.actor_identity_id)
            .where(and_(*conds) if conds else True)
            .order_by(desc(AuditEvent.created_at))
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows]


class ReportRepository:
    """Marketplace reports."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        agent_id: uuid.UUID,
        reporter_identity_id: uuid.UUID,
        reason: str,
        detail: str | None,
    ) -> AgentReport:
        row = AgentReport(
            agent_id=agent_id,
            reporter_identity_id=reporter_identity_id,
            reason=reason,
            detail=detail,
        )
        self.session.add(row)
        await self.session.flush([row])
        return row

    async def get(self, report_id: uuid.UUID) -> AgentReport | None:
        return (
            await self.session.execute(
                select(AgentReport).where(AgentReport.id == report_id)
            )
        ).scalar_one_or_none()

    async def list_for_triage(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[AgentReport, Agent | None, Identity | None, Identity | None]]:
        """Return (report, agent, reporter, reviewer)."""
        reviewer = Identity.__table__.alias("reviewer")
        reporter = Identity.__table__.alias("reporter")
        stmt = (
            select(
                AgentReport,
                Agent,
                reporter.c.id.label("reporter_id"),
                reporter.c.name.label("reporter_name"),
                reviewer.c.id.label("reviewer_id"),
                reviewer.c.name.label("reviewer_name"),
            )
            .outerjoin(Agent, Agent.id == AgentReport.agent_id)
            .outerjoin(reporter, reporter.c.id == AgentReport.reporter_identity_id)
            .outerjoin(reviewer, reviewer.c.id == AgentReport.reviewed_by_identity_id)
            .order_by(desc(AgentReport.created_at))
            .offset(offset)
            .limit(limit)
        )
        if status:
            stmt = stmt.where(AgentReport.status == status)
        rows = (await self.session.execute(stmt)).all()
        out: list[tuple[AgentReport, Agent | None, Identity | None, Identity | None]] = []
        for row in rows:
            # Bundle tuple in (report, agent, reporter_stub, reviewer_stub) form.
            report = row[0]
            agent = row[1]
            reporter_stub = None
            if row.reporter_id is not None:
                stub = Identity()
                stub.id = row.reporter_id  # type: ignore[assignment]
                stub.name = row.reporter_name  # type: ignore[assignment]
                reporter_stub = stub
            reviewer_stub = None
            if row.reviewer_id is not None:
                stub = Identity()
                stub.id = row.reviewer_id  # type: ignore[assignment]
                stub.name = row.reviewer_name  # type: ignore[assignment]
                reviewer_stub = stub
            out.append((report, agent, reporter_stub, reviewer_stub))
        return out

    async def decide(
        self,
        *,
        report: AgentReport,
        decision: ReportStatus,
        note: str | None,
        reviewer_identity_id: uuid.UUID,
    ) -> AgentReport:
        report.status = decision
        report.review_decision = note
        report.reviewed_by_identity_id = reviewer_identity_id
        await self.session.flush([report])
        # Same TimestampMixin.onupdate trap as AsyncRepository.update()
        # — refresh so ``updated_at`` materializes before Pydantic serialization.
        await self.session.refresh(report)
        return report
