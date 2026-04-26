"""Agent routes: CRUD + /recent + /starred + /star."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.agents.kernels.registry import describe as describe_runtimes
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.repositories.agent import AgentRepository
from app.schemas.agent import (
    AgentCloneIn,
    AgentCreate,
    AgentPublicCard,
    AgentRead,
    AgentRecent,
    AgentUpdate,
    StarAgentOut,
)
from app.schemas.audit import AgentReportIn, AgentReportRead
from app.services import agent as svc
from app.services import audit as audit_svc
from app.services import moderation as mod_svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Runtime discovery ───────────────────────────────────
@router.get("/runtimes", summary="List registered Agent Runtimes")
async def list_runtimes() -> dict:
    """Enumerate every Agent Runtime registered in this deployment.

    Returns the runtime kind + display metadata + capabilities. Used by
    the Agent create/edit form to build the runtime picker, and by the
    workspace ``/settings/runtimes`` page to render the capability
    comparison cards.

    Public on purpose: the set of available runtimes is not sensitive
    (the kinds are visible in the frontend bundle anyway) and keeping
    this unauthenticated lets the login screen show a "Powered by: ..."
    footer without first exchanging a token.
    """
    runtimes = describe_runtimes()
    return {"runtimes": runtimes, "count": len(runtimes)}


# ─── List / create ───────────────────────────────────────
@router.get("", response_model=list[AgentRead])
async def list_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[AgentRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agents = await AgentRepository(db).list_visible(
        workspace_id=ws_id, identity_id=identity_id, offset=offset, limit=limit
    )
    return [AgentRead.model_validate(a) for a in agents]


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.create_agent(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        **body.model_dump(),
    )
    await audit_svc.record(
        db,
        action="agent.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent.id,
        summary=f"created agent {agent.name!r}",
        metadata={"visibility": agent.visibility, "backend": agent.backend_kind},
        request=request,
    )
    await db.commit()
    return AgentRead.model_validate(agent)


# ─── Recent / starred (sidebar) ──────────────────────────
@router.get("/recent", response_model=list[AgentRecent])
async def list_recent_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(5, ge=1, le=50),
) -> list[AgentRecent]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await AgentRepository(db).recent_for_identity(
        workspace_id=ws_id, identity_id=identity_id, limit=limit
    )
    out: list[AgentRecent] = []
    for agent, last_at, msg_count, starred, pinned in rows:
        item = AgentRecent.model_validate(agent)
        item.last_message_at = last_at
        item.message_count = msg_count
        item.starred = starred
        item.pinned = pinned
        out.append(item)
    return out


# ─── Marketplace / discover ──────────────────────────────
@router.get("/discover", response_model=list[AgentPublicCard])
async def discover_public_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    q: str | None = Query(None, max_length=128),
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=200),
) -> list[AgentPublicCard]:
    """Public agents across the platform, sorted by popularity (stars)."""
    # Caller must be in some workspace to access the marketplace.
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await AgentRepository(db).list_public_for_discovery(
        q=q, offset=offset, limit=limit
    )
    out: list[AgentPublicCard] = []
    for agent, stars in rows:
        card = AgentPublicCard.model_validate(agent)
        card.stars = stars
        out.append(card)
    return out


@router.post(
    "/{agent_id}/clone",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
)
async def clone_agent(
    agent_id: uuid.UUID,
    body: AgentCloneIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    """Clone a public agent into my active workspace."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    cloned = await svc.clone_public_agent(
        db,
        source_id=agent_id,
        target_workspace_id=ws_id,
        created_by=identity_id,
        name_override=body.name,
    )
    await audit_svc.record(
        db,
        action="marketplace.clone",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=cloned.id,
        summary=f"cloned agent {cloned.name!r} from marketplace",
        metadata={"source_id": str(agent_id)},
        request=request,
    )
    await db.commit()
    return AgentRead.model_validate(cloned)


@router.post(
    "/{agent_id}/report",
    response_model=AgentReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def report_agent(
    agent_id: uuid.UUID,
    body: AgentReportIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentReportRead:
    """Report a public agent for moderation review.

    No workspace gate — anyone with a valid session can flag a public agent.
    Audit row is tagged with the reporter's active workspace so their own
    workspace admin can see the action in the audit feed.
    """
    report = await mod_svc.submit_report(
        db,
        agent_id=agent_id,
        reporter_identity_id=identity_id,
        reason=body.reason,
        detail=body.detail,
    )
    await audit_svc.record(
        db,
        action="agent.report",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="agent",
        resource_id=agent_id,
        summary=f"reported agent {agent_id} ({body.reason})",
        metadata={"report_id": str(report.id), "reason": body.reason},
        request=request,
    )
    await db.commit()
    return AgentReportRead.model_validate(report)


@router.get("/starred", response_model=list[AgentRead])
async def list_starred_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[AgentRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agents = await AgentRepository(db).starred_for_identity(
        workspace_id=ws_id, identity_id=identity_id
    )
    return [AgentRead.model_validate(a) for a in agents]


# ─── Single resource ─────────────────────────────────────
@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)

    patch = body.model_dump(exclude_none=True)
    old_visibility = agent.visibility
    updated = await AgentRepository(db).update(agent, **patch)

    # Visibility transitions (esp. → public) are high-signal audit events.
    if "visibility" in patch and patch["visibility"] != old_visibility:
        await audit_svc.record(
            db,
            action="agent.visibility_change",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="agent",
            resource_id=updated.id,
            summary=f"visibility {old_visibility} → {updated.visibility}",
            metadata={"from": old_visibility, "to": updated.visibility},
            request=request,
        )
    else:
        await audit_svc.record(
            db,
            action="agent.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="agent",
            resource_id=updated.id,
            summary=f"updated agent {updated.name!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
    return AgentRead.model_validate(updated)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    await AgentRepository(db).soft_delete(agent)
    await audit_svc.record(
        db,
        action="agent.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent.id,
        summary=f"deleted agent {agent.name!r}",
        request=request,
    )
    await db.commit()


# ─── Star / pin ──────────────────────────────────────────
@router.post("/{agent_id}/star", response_model=StarAgentOut)
async def star_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    pinned: bool = Query(False),
) -> StarAgentOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    starred, pinned_state = await svc.star_agent(
        db, identity_id=identity_id, agent_id=agent_id, pinned=pinned
    )
    await db.commit()
    return StarAgentOut(agent_id=agent_id, starred=starred, pinned=pinned_state)


@router.delete("/{agent_id}/star", status_code=status.HTTP_204_NO_CONTENT)
async def unstar_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    await svc.unstar_agent(db, identity_id=identity_id, agent_id=agent_id)
    await db.commit()
