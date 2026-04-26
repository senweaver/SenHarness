"""Marketplace moderation endpoints.

* ``POST /agents/{id}/report`` — any authenticated user reports a public agent.
  (Mounted on the Agents router at ``app.api.v1.agents`` for URL cohesion.)

* ``GET  /moderation/reports`` — admin triage queue (platform admin OR the
  owner/admin of the workspace that hosts the reported agent).

* ``PATCH /moderation/reports/{id}`` — decide a report.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import PermissionDenied, Unauthorized
from app.db.models.agent_report import ReportStatus
from app.db.models.identity import PlatformRole
from app.db.models.role import BuiltinRole
from app.repositories.audit import ReportRepository
from app.repositories.identity import IdentityRepository
from app.schemas.audit import (
    AgentReportDecide,
    AgentReportEnriched,
)
from app.services import audit as audit_svc
from app.services import moderation as mod_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/moderation", tags=["moderation"])


async def _is_platform_admin(db, identity_id: uuid.UUID) -> bool:
    ident = await IdentityRepository(db).get(identity_id)
    return (
        ident is not None
        and ident.platform_role == PlatformRole.PLATFORM_ADMIN
    )


async def _require_moderator(
    db,
    identity_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
) -> bool:
    """Platform admin OR workspace owner/admin of caller's active workspace."""
    if await _is_platform_admin(db, identity_id):
        return True
    if workspace_id is None:
        raise Unauthorized(
            "no_active_workspace", code="auth.no_active_workspace"
        )
    mem = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    if mem.role not in {BuiltinRole.OWNER.value, BuiltinRole.ADMIN.value}:
        raise PermissionDenied(
            "moderator_required", code="moderation.moderator_required"
        )
    return False  # workspace-scoped moderator


@router.get("/reports", response_model=list[AgentReportEnriched])
async def list_reports(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    status: str | None = Query(None, pattern="^(pending|reviewed|dismissed|removed)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AgentReportEnriched]:
    platform_admin = await _require_moderator(db, identity_id, workspace_id)
    rows = await ReportRepository(db).list_for_triage(
        status=status, limit=limit, offset=offset
    )

    out: list[AgentReportEnriched] = []
    for report, agent, reporter, reviewer in rows:
        # Workspace-scoped moderators can only see reports for agents in their
        # active workspace. Platform admins see everything.
        if (
            not platform_admin
            and (agent is None or agent.workspace_id != workspace_id)
        ):
            continue
        card = AgentReportEnriched.model_validate(report)
        card.agent_name = agent.name if agent else None
        card.agent_workspace_id = agent.workspace_id if agent else None
        card.reporter_name = reporter.name if reporter else None
        card.reviewer_name = reviewer.name if reviewer else None
        out.append(card)
    return out


@router.patch(
    "/reports/{report_id}",
    response_model=AgentReportEnriched,
)
async def decide_report(
    report_id: uuid.UUID,
    body: AgentReportDecide,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AgentReportEnriched:
    await _require_moderator(db, identity_id, workspace_id)

    updated = await mod_svc.decide_report(
        db,
        report_id=report_id,
        decision=ReportStatus(body.decision),
        note=body.note,
        reviewer_identity_id=identity_id,
    )

    await audit_svc.record(
        db,
        action="report.decide",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="report",
        resource_id=updated.id,
        summary=f"Report on agent {updated.agent_id} decided: {body.decision}",
        metadata={
            "decision": body.decision,
            "agent_id": str(updated.agent_id),
            "reason": updated.reason,
            "note": body.note,
        },
    )
    await db.commit()

    card = AgentReportEnriched.model_validate(updated)
    card.agent_workspace_id = None
    return card
