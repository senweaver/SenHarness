"""Squad service: CRUD + member management."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.models.squad import Squad, SquadStrategy
from app.repositories.agent import AgentRepository
from app.repositories.squad import SquadMemberRepository, SquadRepository


async def create_squad(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    description: str | None,
    strategy: SquadStrategy,
    config_json: dict,
    members: list[tuple[uuid.UUID, str, int]],
) -> Squad:
    # Validate all member agents belong to this workspace.
    agent_repo = AgentRepository(session)
    for agent_id, _, _ in members:
        agent = await agent_repo.get(agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise Conflict(
                f"agent_not_in_workspace: {agent_id}",
                code="squad.agent_not_in_workspace",
            )

    squad_repo = SquadRepository(session)
    squad = await squad_repo.create(
        workspace_id=workspace_id,
        created_by=created_by,
        name=name,
        description=description,
        strategy=strategy,
        config_json=config_json,
    )

    if members:
        await SquadMemberRepository(session).replace_members(
            squad_id=squad.id, members=members
        )
    return squad


async def get_or_404(
    session: AsyncSession, squad_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Squad:
    squad = await SquadRepository(session).get(squad_id)
    if squad is None or squad.workspace_id != workspace_id:
        raise NotFound("squad_not_found", code="squad.not_found")
    return squad


async def list_squads(
    session: AsyncSession, *, workspace_id: uuid.UUID, limit: int = 100
) -> list[Squad]:
    return list(
        await SquadRepository(session).list(workspace_id=workspace_id, limit=limit)
    )


async def update_squad(
    session: AsyncSession, *, squad: Squad, **patch
) -> Squad:
    return await SquadRepository(session).update(squad, **patch)


async def replace_members(
    session: AsyncSession,
    *,
    squad_id: uuid.UUID,
    members: list[tuple[uuid.UUID, str, int]],
) -> None:
    await SquadMemberRepository(session).replace_members(
        squad_id=squad_id, members=members
    )
