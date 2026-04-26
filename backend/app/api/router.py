"""Aggregate v1 routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agents,
    approvals,
    attachments,
    audit,
    auth,
    backends,
    batch,
    channels,
    departments,
    flows,
    governance,
    gw_openclaw,
    health,
    hooks,
    kb_sources,
    keyring,
    knowledge,
    mcp,
    me,
    memory,
    memory_profiles,
    metrics,
    moderation,
    notifications,
    openai_compat,
    providers,
    runtimes,
    secrets,
    sessions,
    skills,
    skills_persistence,
    squads,
    traces,
    version,
    workspaces,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(version.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(squads.router, prefix="/squads", tags=["squads"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(memory_profiles.router)
api_router.include_router(mcp.router)
api_router.include_router(metrics.router)
api_router.include_router(notifications.router)
api_router.include_router(audit.router)
api_router.include_router(moderation.router)
api_router.include_router(channels.router)
api_router.include_router(flows.router)
api_router.include_router(governance.router)
api_router.include_router(hooks.router)
api_router.include_router(knowledge.router)
api_router.include_router(kb_sources.router)
api_router.include_router(attachments.router)
api_router.include_router(skills.router, prefix="/skills", tags=["skills"])
api_router.include_router(skills_persistence.router)
api_router.include_router(approvals.router)
api_router.include_router(secrets.router)
api_router.include_router(departments.router)
api_router.include_router(admin.router)
api_router.include_router(backends.router)
api_router.include_router(gw_openclaw.router)
api_router.include_router(keyring.router)
api_router.include_router(batch.router)
api_router.include_router(openai_compat.router)
api_router.include_router(runtimes.router)
api_router.include_router(traces.router)
