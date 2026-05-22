"""M3.4 — agent profile read + manual refresh + platform-admin cross-workspace.

Three routes:

* ``GET    /agents/{agent_id}/profile`` — workspace member read; the
  response intentionally omits the ``cross_workspace_stats_json``
  field. 404 when no profile row has been computed yet.
* ``POST   /agents/{agent_id}/profile/refresh`` — workspace admin
  synchronously recomputes the row (ARQ sweep is daily — admin may
  want a fresh aggregate before the next tick).
* ``GET    /admin/agents/{agent_id}/profile/cross-workspace`` —
  platform-admin only. Returns the same row plus
  ``cross_workspace_stats_json``; the service layer is the gate so
  even a misrouted call cannot leak the field.

Each route declares its own per-bucket rate limit; never share a
bucket between read and refresh because the brief explicitly demands
the refresh budget stay tight (``5/300s``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentIdentityId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity, PlatformRole
from app.repositories.identity import IdentityRepository
from app.schemas.agent_profile import (
    AgentProfileAdminRead,
    AgentProfileRead,
    AgentProfileRefreshResult,
)
from app.services import agent_profile as agent_profile_svc
from app.services import audit as audit_svc
from app.services import workspace as ws_svc

router = APIRouter(tags=["agents", "agent-profile"])


# ─── Auth gates ──────────────────────────────────────────────
async def _resolve_identity(db: DBSession, identity_id: uuid.UUID) -> Identity:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise Unauthorized("identity_missing", code="auth.no_identity")
    return identity


async def _require_workspace_member(
    *,
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: uuid.UUID,
) -> Identity:
    identity = await _resolve_identity(db, identity_id)
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    return identity


async def _require_workspace_admin(
    *,
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: uuid.UUID,
) -> Identity:
    identity = await _resolve_identity(db, identity_id)
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    return identity


async def _resolve_agent_workspace(db: DBSession, *, agent_id: uuid.UUID) -> uuid.UUID:
    """Return the workspace_id of the agent (or 404)."""
    from app.db.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise NotFound("agent_missing", code="agent.not_found")
    return agent.workspace_id


# ─── Routes ──────────────────────────────────────────────────
@router.get(
    "/agents/{agent_id}/profile",
    response_model=AgentProfileRead,
    dependencies=[
        Depends(rate_limit("agent_profile_read", limit=60, period_seconds=60)),
    ],
)
async def get_agent_profile(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> AgentProfileRead:
    """Workspace-member read. Never carries cross-workspace stats."""
    workspace_id = await _resolve_agent_workspace(db, agent_id=agent_id)
    await _require_workspace_member(workspace_id=workspace_id, db=db, identity_id=identity_id)
    profile = await agent_profile_svc.get_profile(db, workspace_id=workspace_id, agent_id=agent_id)
    if profile is None:
        raise NotFound(
            "agent_profile_missing",
            code="agent_profile.not_found",
        )
    return AgentProfileRead.model_validate(profile)


@router.post(
    "/agents/{agent_id}/profile/refresh",
    response_model=AgentProfileRefreshResult,
    dependencies=[
        Depends(
            rate_limit(
                "agent_profile_refresh",
                limit=5,
                period_seconds=300,
            )
        ),
    ],
)
async def refresh_agent_profile(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> AgentProfileRefreshResult:
    """Workspace-admin synchronous refresh.

    Runs :func:`update_profile_for_agent` in the request session and
    additionally writes ``agent_profile.refresh_triggered`` so the
    UI can distinguish admin-driven runs from cron runs without
    spelunking the audit feed.
    """
    _ = request
    workspace_id = await _resolve_agent_workspace(db, agent_id=agent_id)
    actor = await _require_workspace_admin(
        workspace_id=workspace_id, db=db, identity_id=identity_id
    )

    outcome = await agent_profile_svc.update_profile_for_agent(
        db,
        workspace_id=workspace_id,
        agent_id=agent_id,
        since_days=agent_profile_svc.DEFAULT_SINCE_DAYS,
        invocation_kind="manual",
        actor_identity_id=actor.id,
    )

    await audit_svc.record(
        db,
        action=agent_profile_svc.AUDIT_REFRESH_TRIGGERED,
        actor_identity_id=actor.id,
        workspace_id=workspace_id,
        resource_type="agent_profile",
        resource_id=outcome.profile.id,
        summary=(
            f"agent_profile manual refresh triggered: "
            f"{outcome.aggregated_run_count} runs aggregated"
        ),
        metadata={
            "agent_id": str(agent_id),
            "since_days": int(agent_profile_svc.DEFAULT_SINCE_DAYS),
            "duration_ms": int(outcome.duration_ms),
            "aux_skipped": bool(outcome.aux_skipped),
            "aux_skip_reason": outcome.aux_skip_reason,
        },
    )

    await db.commit()

    return AgentProfileRefreshResult(
        workspace_id=workspace_id,
        agent_id=agent_id,
        last_aggregated_at=outcome.profile.last_aggregated_at,
        aggregated_run_count=int(outcome.aggregated_run_count),
        sample_size=int(outcome.sample_size),
        strengths_json=dict(outcome.profile.strengths_json or {}),
        failure_modes_json=dict(outcome.profile.failure_modes_json or {}),
        aux_skipped=bool(outcome.aux_skipped),
        aux_skip_reason=outcome.aux_skip_reason,
    )


@router.get(
    "/admin/agents/{agent_id}/profile/cross-workspace",
    response_model=AgentProfileAdminRead,
    dependencies=[
        Depends(
            rate_limit(
                "agent_profile_admin_read",
                limit=30,
                period_seconds=60,
            )
        ),
    ],
)
async def get_agent_profile_cross_workspace(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> AgentProfileAdminRead:
    """Platform-admin only. Returns the row including cross-workspace stats."""
    actor = await _resolve_identity(db, identity_id)
    profile = await agent_profile_svc.get_profile_with_cross_workspace_stats(
        db, agent_id=agent_id, actor=actor
    )
    await db.commit()
    return AgentProfileAdminRead.model_validate(profile)
