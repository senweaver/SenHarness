"""Aggregate v1 routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    admin_evolver,
    admin_jobs,
    admin_platform_settings,
    admin_plugin,
    admin_retention,
    admin_runtime,
    admin_workspace_quota,
    agent_profile,
    agent_runtime,
    agents,
    approvals,
    attachments,
    audit,
    auth,
    aux_config,
    backends,
    batch,
    channels,
    departments,
    flows,
    governance,
    gw_openclaw,
    health,
    hooks,
    hub_skill,
    insights,
    kb_sources,
    keyring,
    knowledge,
    lineage,
    mcp,
    me,
    memory,
    memory_profiles,
    metrics,
    moderation,
    notifications,
    onboarding,
    openai_compat,
    pending_memories,
    project_boards,
    provider_catalog,
    providers,
    public,
    runtimes,
    search_providers,
    secrets,
    session_artifacts,
    sessions,
    sidebar,
    skill_graph,
    skill_usage,
    skills,
    skills_evolve,
    skills_persistence,
    skills_verify,
    squads,
    threads,
    tools,
    traces,
    version,
    workspaces,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(version.router, tags=["health"])
api_router.include_router(public.router, prefix="/public", tags=["public"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(agent_profile.router)
api_router.include_router(agent_runtime.router)
api_router.include_router(squads.router, prefix="/squads", tags=["squads"])
api_router.include_router(project_boards.router, tags=["project_boards"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(session_artifacts.router, tags=["sessions"])
api_router.include_router(lineage.router, tags=["sessions"])
api_router.include_router(aux_config.router, tags=["workspaces"])
api_router.include_router(providers.router, prefix="/providers", tags=["providers"])
api_router.include_router(provider_catalog.router, prefix="/provider-catalog", tags=["providers"])
api_router.include_router(search_providers.router, prefix="/search-providers", tags=["providers"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(memory_profiles.router)
api_router.include_router(pending_memories.router)
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
api_router.include_router(skills_persistence.diff_router)
api_router.include_router(skill_graph.router)
api_router.include_router(skill_usage.router)
api_router.include_router(skills_evolve.router)
api_router.include_router(skills_verify.router)
api_router.include_router(hub_skill.router)
api_router.include_router(hub_skill.admin_router)
api_router.include_router(insights.router)
api_router.include_router(approvals.router)
api_router.include_router(secrets.router)
api_router.include_router(departments.router)
api_router.include_router(admin.router)
api_router.include_router(admin_jobs.router)
api_router.include_router(admin_platform_settings.router)
api_router.include_router(admin_retention.router)
api_router.include_router(admin_workspace_quota.router)
api_router.include_router(admin_evolver.router)
api_router.include_router(admin_runtime.router)
api_router.include_router(admin_plugin.router)
api_router.include_router(backends.router)
api_router.include_router(gw_openclaw.router)
api_router.include_router(keyring.router)
api_router.include_router(batch.router)
api_router.include_router(openai_compat.router)
api_router.include_router(runtimes.router)
api_router.include_router(threads.router)
api_router.include_router(tools.router)
api_router.include_router(traces.router)
api_router.include_router(sidebar.router, prefix="/sidebar", tags=["sidebar"])
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
