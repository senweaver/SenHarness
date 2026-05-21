"""Agent service: CRUD + starring + recent/pinned queries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind
from app.repositories.agent import AgentRepository, AgentStarRepository

DEFAULT_AGENT_DESCRIPTION = "默认智能体，随时为你服务。"  # noqa: RUF001
DEFAULT_AGENT_PERSONA = (
    "# 默认人格\n\n"
    "你是 SenHarness 的默认智能体，目标是用最简短、最直接的方式完成用户的请求。\n\n"  # noqa: RUF001
    "原则：\n"  # noqa: RUF001
    "- 先给结果，再给解释。\n"  # noqa: RUF001
    "- 不确定时主动澄清，不乱猜。\n"  # noqa: RUF001
    "- 涉及写入、执行、外部调用时主动说明风险。\n"
)


def default_agent_name() -> str:
    """Canonical name used by the per-workspace default agent."""
    return "默认助手"


async def ensure_default_agent(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID | None,
) -> Agent:
    """Idempotent: every workspace must have a working default agent.

    Returns the existing row if one already matches the canonical name,
    otherwise creates a fresh ``native`` / ``L2`` / workspace-visible agent.
    Used by ``seed.py``, ``create_workspace`` and the alembic backfill path
    so a freshly-registered tenant never lands on ``session.no_agent``.
    """
    name = default_agent_name()
    repo = AgentRepository(session)
    existing = await repo.get_by(workspace_id=workspace_id, name=name)
    if existing is not None:
        return existing
    return await create_agent(
        session,
        workspace_id=workspace_id,
        created_by=created_by,
        name=name,
        description=DEFAULT_AGENT_DESCRIPTION,
        persona_md=DEFAULT_AGENT_PERSONA,
        backend_kind=BackendKind.NATIVE,
        visibility=AgentVisibility.WORKSPACE,
        autonomy_level=AutonomyLevel.L2,
    )


async def create_agent(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID | None,
    name: str,
    description: str | None = None,
    persona_md: str | None = None,
    backend_kind: str = BackendKind.NATIVE,
    visibility: AgentVisibility = AgentVisibility.WORKSPACE,
    autonomy_level: AutonomyLevel = AutonomyLevel.L2,
    **extras: object,
) -> Agent:
    from app.services import stars as stars_svc

    payload = {
        "workspace_id": workspace_id,
        "created_by": created_by,
        "name": name,
        "description": description,
        "persona_md": persona_md,
        "backend_kind": backend_kind,
        "visibility": visibility,
        "autonomy_level": autonomy_level,
        **extras,
    }
    agent = await AgentRepository(session).create(**payload)
    await session.flush()
    await stars_svc.fan_out_agent_to_workspace_members(
        session, workspace_id=workspace_id, agent_id=agent.id
    )
    return agent


async def get_agent_or_404(
    session: AsyncSession, agent_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Agent:
    agent = await AgentRepository(session).get(agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise NotFound("agent_not_found", code="agent.not_found")
    return agent


async def star_agent(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    agent_id: uuid.UUID,
    pinned: bool = False,
) -> tuple[bool, bool]:
    """Idempotent. Returns `(starred, pinned)` post-state."""
    repo = AgentStarRepository(session)
    existing = await repo.get_for(identity_id, agent_id)
    if existing is None:
        await repo.create(identity_id=identity_id, agent_id=agent_id, pinned=pinned)
        return True, pinned
    if existing.pinned != pinned:
        await repo.update(existing, pinned=pinned)
    return True, pinned


async def unstar_agent(
    session: AsyncSession, *, identity_id: uuid.UUID, agent_id: uuid.UUID
) -> bool:
    repo = AgentStarRepository(session)
    existing = await repo.get_for(identity_id, agent_id)
    if existing is None:
        return False
    await repo.hard_delete(existing)
    return True


# Keys inside ``metadata_json`` that belong to the loader-managed
# template lifecycle. Stripped when a user clones a built-in template
# so their copy is treated as a regular workspace agent (and a
# subsequent ``seed-templates`` run doesn't accidentally upsert into
# the user's clone via ``template_slug``).
_TEMPLATE_OWNED_KEYS: frozenset[str] = frozenset(
    {"template", "template_slug", "category", "tags", "color"}
)


async def clone_public_agent(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    target_workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    name_override: str | None = None,
) -> Agent:
    """Clone a PUBLIC agent (or one already visible to the caller) into the
    target workspace.

    All user-facing config travels: persona, avatar, backend, autonomy, and
    the full ``metadata_json`` (sandbox / skills / approvals / code_mode).
    Version history + stars are NOT copied — this is a fresh agent owned by
    the cloner. Template-bookkeeping keys (``template``, ``template_slug``,
    ``category``, ``tags``, ``color``) are stripped: the clone is a
    regular user agent, not another copy of the built-in template.
    """
    from app.core.errors import PermissionDenied

    src = await AgentRepository(session).get(source_id)
    if src is None or src.deleted_at is not None:
        raise NotFound("agent_not_found", code="agent.not_found")

    if src.visibility != AgentVisibility.PUBLIC and src.workspace_id != target_workspace_id:
        raise PermissionDenied("not_public", code="agent.not_public")

    src_meta = dict(src.metadata_json or {})
    cloned_meta = {
        k: v for k, v in src_meta.items() if k not in _TEMPLATE_OWNED_KEYS
    }

    return await AgentRepository(session).create(
        workspace_id=target_workspace_id,
        created_by=created_by,
        name=name_override or f"{src.name} (copy)",
        description=src.description,
        persona_md=src.persona_md,
        avatar_url=src.avatar_url,
        backend_kind=src.backend_kind,
        autonomy_level=src.autonomy_level,
        visibility=AgentVisibility.WORKSPACE,  # clones default to workspace, not public
        skill_refs_json=list(src.skill_refs_json or []),
        memory_config_json=dict(src.memory_config_json or {}),
        quotas_json=dict(src.quotas_json or {}),
        metadata_json=cloned_meta,
        default_model=src.default_model,
        default_search_provider_kind=src.default_search_provider_kind,
    )
