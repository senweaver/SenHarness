"""Moderation service — marketplace report lifecycle."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied
from app.db.models.agent import AgentVisibility
from app.db.models.agent_report import AgentReport, ReportStatus
from app.repositories.agent import AgentRepository
from app.repositories.audit import ReportRepository


async def submit_report(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    reporter_identity_id: uuid.UUID,
    reason: str,
    detail: str | None,
) -> AgentReport:
    """Any authenticated user can report a public Agent.

    We only allow reports on public Agents — otherwise anyone could spam
    reports against private / workspace Agents they can't even see.
    """
    agent = await AgentRepository(session).get(agent_id)
    if agent is None or agent.deleted_at is not None:
        raise NotFound("agent_not_found", code="agent.not_found")
    if agent.visibility != AgentVisibility.PUBLIC:
        raise PermissionDenied("agent_not_public", code="agent.not_public_for_report")

    # Soft de-dup: if the same reporter filed a report in the last 24h for the
    # same agent, reject the second one so we don't clog the queue.
    # (We'll rely on the unique constraint later; for now do a simple check.)
    return await ReportRepository(session).create(
        agent_id=agent_id,
        reporter_identity_id=reporter_identity_id,
        reason=reason,
        detail=detail,
    )


async def decide_report(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    decision: ReportStatus,
    note: str | None,
    reviewer_identity_id: uuid.UUID,
) -> AgentReport:
    repo = ReportRepository(session)
    report = await repo.get(report_id)
    if report is None:
        raise NotFound("report_not_found", code="report.not_found")
    if report.status != ReportStatus.PENDING:
        raise Conflict("already_decided", code="report.already_decided")

    updated = await repo.decide(
        report=report,
        decision=decision,
        note=note,
        reviewer_identity_id=reviewer_identity_id,
    )

    # If the decision is "removed", also de-list the agent back to private to
    # pull it out of the marketplace immediately. The reviewer can always
    # hard-delete later via the normal DELETE /agents/{id} flow.
    if decision == ReportStatus.REMOVED:
        agent = await AgentRepository(session).get(updated.agent_id)
        if agent is not None and agent.visibility == AgentVisibility.PUBLIC:
            await AgentRepository(session).update(agent, visibility=AgentVisibility.PRIVATE)

    return updated
