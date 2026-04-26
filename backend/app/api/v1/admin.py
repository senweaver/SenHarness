"""Platform admin console — cross-workspace operations.

Every endpoint here requires ``identity.platform_role == PLATFORM_ADMIN`` —
this is **not** the same as workspace ``owner``/``admin``. Workspace admins
only see their own tenant; platform admins see every workspace and every
identity across the deployment.

Scope:
    /admin/stats               — global counters for the dashboard
    /admin/identities          — list + detail + patch (status / platform_role)
    /admin/workspaces          — list + detail + patch (plan / branding) + soft delete

Audit: every mutation writes an ``admin.*`` audit row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.api.deps import CurrentIdentityId, DBSession
from app.core.security import utcnow_naive
from app.db.models.agent import Agent
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.audit import AuditEvent
from app.db.models.channel import Channel
from app.db.models.flow import Flow
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership
from app.db.models.message import Message
from app.db.models.session import Session as SessionModel
from app.db.models.workspace import Workspace, WorkspacePlan
from app.repositories.approval import ApprovalRepository
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas._base import ORMModel, Timestamped
from app.schemas.approval import ApprovalDecision, ApprovalRead
from app.services import audit as audit_svc

router = APIRouter(prefix="/admin", tags=["admin"])


# ─── Gate ────────────────────────────────────────────────
async def require_platform_admin(
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> Identity:
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None or ident.platform_role != PlatformRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform_admin_required",
        )
    return ident


AdminGate = Depends(require_platform_admin)


# ─── DTOs ─────────────────────────────────────────────────
class GlobalStats(BaseModel):
    identities_total: int
    identities_active: int
    identities_suspended: int
    platform_admins: int
    workspaces_total: int
    workspaces_active: int
    sessions_total: int
    messages_total: int
    agents_total: int
    flows_total: int
    channels_total: int
    audit_events_24h: int
    new_identities_7d: int
    new_workspaces_7d: int


class IdentityAdminRow(Timestamped):
    email: str
    name: str
    avatar_url: str | None = None
    status: IdentityStatus
    platform_role: PlatformRole
    oauth_provider: str | None = None
    workspace_count: int = 0


class WorkspaceBrief(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str


class IdentityAdminDetail(IdentityAdminRow):
    workspaces: list[WorkspaceBrief] = Field(default_factory=list)


class IdentityPatch(ORMModel):
    status: IdentityStatus | None = None
    platform_role: PlatformRole | None = None


class WorkspaceAdminRow(Timestamped):
    name: str
    slug: str
    description: str | None = None
    plan: WorkspacePlan
    member_count: int = 0
    agent_count: int = 0
    session_count: int = 0


class WorkspaceAdminDetail(WorkspaceAdminRow):
    branding_json: dict = Field(default_factory=dict)
    home_config_json: dict = Field(default_factory=dict)
    quota_json: dict = Field(default_factory=dict)


class WorkspacePatch(ORMModel):
    name: str | None = None
    description: str | None = None
    plan: WorkspacePlan | None = None
    branding_json: dict | None = None
    quota_json: dict | None = None


# ─── Stats ────────────────────────────────────────────────
@router.get("/stats", response_model=GlobalStats)
async def get_global_stats(
    db: DBSession,
    _admin: Identity = AdminGate,
) -> GlobalStats:
    now = datetime.now(UTC).replace(tzinfo=None)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    async def _count(stmt) -> int:
        return int((await db.execute(stmt)).scalar() or 0)

    identities_total = await _count(
        select(func.count(Identity.id)).where(Identity.deleted_at.is_(None))
    )
    identities_active = await _count(
        select(func.count(Identity.id)).where(
            Identity.deleted_at.is_(None),
            Identity.status == IdentityStatus.ACTIVE,
        )
    )
    identities_suspended = await _count(
        select(func.count(Identity.id)).where(
            Identity.deleted_at.is_(None),
            Identity.status == IdentityStatus.SUSPENDED,
        )
    )
    platform_admins = await _count(
        select(func.count(Identity.id)).where(
            Identity.deleted_at.is_(None),
            Identity.platform_role == PlatformRole.PLATFORM_ADMIN,
        )
    )

    workspaces_total = await _count(
        select(func.count(Workspace.id)).where(Workspace.deleted_at.is_(None))
    )
    workspaces_active = workspaces_total  # no suspended flag on workspaces yet

    sessions_total = await _count(
        select(func.count(SessionModel.id)).where(SessionModel.deleted_at.is_(None))
    )
    messages_total = await _count(select(func.count(Message.id)))
    agents_total = await _count(
        select(func.count(Agent.id)).where(Agent.deleted_at.is_(None))
    )
    flows_total = await _count(
        select(func.count(Flow.id)).where(Flow.deleted_at.is_(None))
    )
    channels_total = await _count(
        select(func.count(Channel.id)).where(Channel.deleted_at.is_(None))
    )
    audit_events_24h = await _count(
        select(func.count(AuditEvent.id)).where(AuditEvent.created_at >= since_24h)
    )
    new_identities_7d = await _count(
        select(func.count(Identity.id)).where(
            Identity.deleted_at.is_(None),
            Identity.created_at >= since_7d,
        )
    )
    new_workspaces_7d = await _count(
        select(func.count(Workspace.id)).where(
            Workspace.deleted_at.is_(None),
            Workspace.created_at >= since_7d,
        )
    )

    return GlobalStats(
        identities_total=identities_total,
        identities_active=identities_active,
        identities_suspended=identities_suspended,
        platform_admins=platform_admins,
        workspaces_total=workspaces_total,
        workspaces_active=workspaces_active,
        sessions_total=sessions_total,
        messages_total=messages_total,
        agents_total=agents_total,
        flows_total=flows_total,
        channels_total=channels_total,
        audit_events_24h=audit_events_24h,
        new_identities_7d=new_identities_7d,
        new_workspaces_7d=new_workspaces_7d,
    )


# ─── Identities ──────────────────────────────────────────
@router.get("/identities", response_model=list[IdentityAdminRow])
async def list_identities(
    db: DBSession,
    _admin: Identity = AdminGate,
    q: str | None = Query(None, description="ILIKE on name + email."),
    status_filter: str | None = Query(None, alias="status"),
    role: str | None = Query(None, description="platform_role filter."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[IdentityAdminRow]:
    conds = [Identity.deleted_at.is_(None)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(Identity.name.ilike(like), Identity.email.ilike(like)))
    if status_filter:
        conds.append(Identity.status == status_filter)
    if role:
        conds.append(Identity.platform_role == role)

    # Joined workspace count via subquery — avoids N+1 lookups.
    ws_count = (
        select(Membership.identity_id, func.count(Membership.id).label("ws_count"))
        .where(Membership.deleted_at.is_(None))
        .group_by(Membership.identity_id)
        .subquery()
    )
    stmt = (
        select(Identity, func.coalesce(ws_count.c.ws_count, 0).label("ws_count"))
        .outerjoin(ws_count, ws_count.c.identity_id == Identity.id)
        .where(and_(*conds))
        .order_by(desc(Identity.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[IdentityAdminRow] = []
    for ident, n in rows:
        card = IdentityAdminRow.model_validate(ident)
        card.workspace_count = int(n or 0)
        out.append(card)
    return out


@router.get("/identities/{identity_id}", response_model=IdentityAdminDetail)
async def get_identity_detail(
    identity_id: uuid.UUID,
    db: DBSession,
    _admin: Identity = AdminGate,
) -> IdentityAdminDetail:
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="identity_not_found"
        )
    stmt = (
        select(Membership, Workspace)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(
            Membership.identity_id == identity_id,
            Membership.deleted_at.is_(None),
            Workspace.deleted_at.is_(None),
        )
    )
    ws_rows = (await db.execute(stmt)).all()

    detail = IdentityAdminDetail.model_validate(ident)
    detail.workspace_count = len(ws_rows)
    detail.workspaces = [
        WorkspaceBrief(id=ws.id, name=ws.name, slug=ws.slug, role=mem.role)
        for mem, ws in ws_rows
    ]
    return detail


@router.patch("/identities/{identity_id}", response_model=IdentityAdminDetail)
async def update_identity(
    identity_id: uuid.UUID,
    body: IdentityPatch,
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> IdentityAdminDetail:
    repo = IdentityRepository(db)
    ident = await repo.get(identity_id)
    if ident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="identity_not_found"
        )
    patch = body.model_dump(exclude_none=True)
    if ident.id == admin.id and patch.get("platform_role") == PlatformRole.USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot_self_demote",
        )

    updated = await repo.update(ident, **patch)
    await audit_svc.record(
        db,
        action="admin.identity.update",
        actor_identity_id=admin.id,
        resource_type="identity",
        resource_id=updated.id,
        summary=f"updated {updated.email!r}",
        metadata={"fields": sorted(patch.keys())},
        request=request,
    )
    await db.commit()
    return await get_identity_detail(updated.id, db, admin)


# ─── Workspaces ──────────────────────────────────────────
@router.get("/workspaces", response_model=list[WorkspaceAdminRow])
async def list_workspaces(
    db: DBSession,
    _admin: Identity = AdminGate,
    q: str | None = Query(None),
    plan: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[WorkspaceAdminRow]:
    conds = [Workspace.deleted_at.is_(None)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(
            or_(
                Workspace.name.ilike(like),
                Workspace.slug.ilike(like),
                Workspace.description.ilike(like),
            )
        )
    if plan:
        conds.append(Workspace.plan == plan)

    member_sq = (
        select(
            Membership.workspace_id,
            func.count(Membership.id).label("member_count"),
        )
        .where(Membership.deleted_at.is_(None))
        .group_by(Membership.workspace_id)
        .subquery()
    )
    agent_sq = (
        select(Agent.workspace_id, func.count(Agent.id).label("agent_count"))
        .where(Agent.deleted_at.is_(None))
        .group_by(Agent.workspace_id)
        .subquery()
    )
    session_sq = (
        select(
            SessionModel.workspace_id,
            func.count(SessionModel.id).label("session_count"),
        )
        .where(SessionModel.deleted_at.is_(None))
        .group_by(SessionModel.workspace_id)
        .subquery()
    )

    stmt = (
        select(
            Workspace,
            func.coalesce(member_sq.c.member_count, 0),
            func.coalesce(agent_sq.c.agent_count, 0),
            func.coalesce(session_sq.c.session_count, 0),
        )
        .outerjoin(member_sq, member_sq.c.workspace_id == Workspace.id)
        .outerjoin(agent_sq, agent_sq.c.workspace_id == Workspace.id)
        .outerjoin(session_sq, session_sq.c.workspace_id == Workspace.id)
        .where(and_(*conds))
        .order_by(desc(Workspace.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[WorkspaceAdminRow] = []
    for ws, m, a, s in rows:
        card = WorkspaceAdminRow.model_validate(ws)
        card.member_count = int(m or 0)
        card.agent_count = int(a or 0)
        card.session_count = int(s or 0)
        out.append(card)
    return out


async def _workspace_counts(
    db, workspace_id: uuid.UUID
) -> tuple[int, int, int]:
    """Return ``(member_count, agent_count, session_count)`` for one workspace."""
    member_count = int(
        (
            await db.execute(
                select(func.count(Membership.id)).where(
                    Membership.workspace_id == workspace_id,
                    Membership.deleted_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    agent_count = int(
        (
            await db.execute(
                select(func.count(Agent.id)).where(
                    Agent.workspace_id == workspace_id,
                    Agent.deleted_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    session_count = int(
        (
            await db.execute(
                select(func.count(SessionModel.id)).where(
                    SessionModel.workspace_id == workspace_id,
                    SessionModel.deleted_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    return member_count, agent_count, session_count


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceAdminDetail)
async def get_workspace_detail(
    workspace_id: uuid.UUID,
    db: DBSession,
    _admin: Identity = AdminGate,
) -> WorkspaceAdminDetail:
    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is None or ws.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found"
        )
    m, a, s = await _workspace_counts(db, workspace_id)
    card = WorkspaceAdminDetail.model_validate(ws)
    card.member_count = m
    card.agent_count = a
    card.session_count = s
    return card


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceAdminDetail)
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspacePatch,
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> WorkspaceAdminDetail:
    repo = WorkspaceRepository(db)
    ws = await repo.get(workspace_id)
    if ws is None or ws.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found"
        )
    patch = body.model_dump(exclude_none=True)
    updated = await repo.update(ws, **patch)
    await audit_svc.record(
        db,
        action="admin.workspace.update",
        actor_identity_id=admin.id,
        workspace_id=updated.id,
        resource_type="workspace",
        resource_id=updated.id,
        summary=f"updated workspace {updated.slug!r}",
        metadata={"fields": sorted(patch.keys())},
        request=request,
    )
    await db.commit()
    return await get_workspace_detail(updated.id, db, admin)


@router.delete(
    "/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_workspace(
    workspace_id: uuid.UUID,
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> None:
    repo = WorkspaceRepository(db)
    ws = await repo.get(workspace_id)
    if ws is None or ws.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found"
        )
    await repo.soft_delete(ws)
    await audit_svc.record(
        db,
        action="admin.workspace.delete",
        actor_identity_id=admin.id,
        workspace_id=ws.id,
        resource_type="workspace",
        resource_id=ws.id,
        summary=f"deleted workspace {ws.slug!r}",
        request=request,
    )
    await db.commit()


# ─── Platform approvals ───────────────────────────────────
# Cross-workspace pending queue + decide endpoint for platform admins.
# Workspace admins go through ``/api/v1/approvals/*`` which scopes to their
# ``X-Workspace-Id`` header. Platform admins need a bypass: they shouldn't
# have to flip workspaces just to clear a runaway tool call in another tenant.


class AdminApprovalRow(ApprovalRead):
    """Pending row enriched with workspace context for cross-tenant listing."""

    workspace_name: str | None = None
    workspace_slug: str | None = None
    requester_name: str | None = None
    requester_email: str | None = None


@router.get("/approvals", response_model=list[AdminApprovalRow])
async def list_all_approvals(
    db: DBSession,
    status_filter: str | None = Query(default="pending", alias="status"),
    workspace_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    admin: Identity = AdminGate,
) -> list[AdminApprovalRow]:
    """Cross-workspace approvals view.

    Defaults to **pending** so the dashboard has a live triage queue; pass
    ``?status=`` empty or any of ``approved|denied|expired|cancelled`` for the
    audit view.
    """
    stmt = (
        select(
            Approval,
            Workspace.name.label("ws_name"),
            Workspace.slug.label("ws_slug"),
            Identity.name.label("req_name"),
            Identity.email.label("req_email"),
        )
        .join(Workspace, Workspace.id == Approval.workspace_id)
        .join(
            Identity,
            Identity.id == Approval.requested_by_identity_id,
            isouter=True,
        )
        .order_by(desc(Approval.created_at))
        .limit(limit)
    )
    if status_filter:
        try:
            stmt = stmt.where(Approval.status == ApprovalStatus(status_filter))
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"bad_status: {status_filter}",
            ) from e
    if workspace_id is not None:
        stmt = stmt.where(Approval.workspace_id == workspace_id)

    rows = (await db.execute(stmt)).all()
    # Bulk-resolve department names, grouped per workspace so we can do one
    # query per workspace instead of per row. Most admin dashboards land on
    # pending queues dominated by a single noisy tenant anyway.
    from collections import defaultdict

    dept_needs: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for ap, *_ in rows:
        if ap.requested_by_identity_id:
            dept_needs[ap.workspace_id].append(ap.requested_by_identity_id)
        if ap.decided_by_identity_id:
            dept_needs[ap.workspace_id].append(ap.decided_by_identity_id)
    approval_repo = ApprovalRepository(db)
    dept_by_ws: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
    for ws_uuid, ident_ids in dept_needs.items():
        dept_by_ws[ws_uuid] = await approval_repo.department_names_for_identities(
            workspace_id=ws_uuid, identity_ids=ident_ids
        )

    out: list[AdminApprovalRow] = []
    for ap, ws_name, ws_slug, req_name, req_email in rows:
        card = AdminApprovalRow.model_validate(ap)
        card.workspace_name = ws_name
        card.workspace_slug = ws_slug
        card.requester_name = req_name
        card.requester_email = req_email
        dept = dept_by_ws.get(ap.workspace_id, {})
        if ap.requested_by_identity_id:
            card.requester_department_name = dept.get(ap.requested_by_identity_id)
        if ap.decided_by_identity_id:
            card.decided_by_department_name = dept.get(ap.decided_by_identity_id)
        out.append(card)
    return out


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalRead)
async def admin_decide_approval(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> ApprovalRead:
    """Platform-admin bypass of the per-workspace decision rules.

    Unlike ``/api/v1/approvals/{id}/decision``, this endpoint doesn't require
    the admin to be a member of the approval's workspace — the platform role
    itself is the authority. Each call audits as
    ``admin.approval.decide`` tagged with the approval's workspace so the
    workspace's own audit log reflects the platform action.
    """
    repo = ApprovalRepository(db)
    row = await repo.get(approval_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval_not_found"
        )

    approved = payload.action == "approve"
    decided = await repo.decide(
        approval_id=approval_id,
        workspace_id=row.workspace_id,
        approved=approved,
        reason=payload.reason,
        decided_by_identity_id=admin.id,
        now=utcnow_naive(),
    )
    if decided is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval_not_found"
        )

    await audit_svc.record(
        db,
        action="admin.approval.decide",
        actor_identity_id=admin.id,
        workspace_id=row.workspace_id,
        resource_type="approval",
        resource_id=row.id,
        summary=(
            f"platform-admin {'approved' if approved else 'denied'} tool "
            f"{row.tool_name!r} (ws={row.workspace_id})"
        ),
        metadata={
            "tool_name": row.tool_name,
            "decision": "approve" if approved else "deny",
            "reason": payload.reason,
            "session_id": str(row.session_id),
        },
        request=request,
    )
    await db.commit()

    # Wake up the parked runner.
    await APPROVAL_MANAGER.decide(
        approval_id,
        approved=approved,
        reason=payload.reason,
        decided_by=admin.id,
    )
    return ApprovalRead.model_validate(decided)


# ─── GC (B2) ──────────────────────────────────────────────
@router.post("/gc/run")
async def admin_gc_run(
    request: Request,
    db: DBSession,
    dry_run: bool = Query(default=True),
    admin: Identity = AdminGate,
) -> dict:
    """Trigger the nightly GC sweep on demand.

    Defaults to ``dry_run=true`` so the admin can preview impact ("would
    delete N rows") before flipping the flag. The same routine is registered
    in APScheduler at 03:00 UTC.
    """
    from app.services import gc as gc_svc

    summary = await gc_svc.run_full_sweep(dry_run=dry_run)
    await audit_svc.record(
        db,
        action="admin.gc.run",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="gc",
        resource_id=None,
        summary=(
            f"{'dry-run' if dry_run else 'executed'} GC: "
            f"att={summary.get('attachments', {}).get('candidates', 0)} "
            f"docs={summary.get('knowledge_docs', {}).get('candidates', 0)} "
            f"approvals={summary.get('approvals', {}).get('candidates', 0)} "
            f"audit={summary.get('audit_events', {}).get('candidates', 0)}"
        ),
        metadata=summary,
        request=request,
    )
    await db.commit()
    return summary
