"""Squad + SquadMember repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.db.models.squad import Squad, SquadMember
from app.db.repository import AsyncRepository


class SquadRepository(AsyncRepository[Squad]):
    model = Squad


class SquadMemberRepository(AsyncRepository[SquadMember]):
    model = SquadMember

    async def list_for_squad(self, squad_id: uuid.UUID) -> Sequence[SquadMember]:
        stmt = (
            select(SquadMember)
            .where(SquadMember.squad_id == squad_id)
            .order_by(SquadMember.weight.desc(), SquadMember.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def replace_members(
        self,
        *,
        squad_id: uuid.UUID,
        members: list[tuple[uuid.UUID, str, int]],
    ) -> None:
        """Full replacement of the member set. Simple strategy for now."""
        # Delete existing rows then recreate.
        await self.hard_delete_where(squad_id=squad_id)
        for agent_id, role, weight in members:
            await self.create(
                squad_id=squad_id, agent_id=agent_id, role_in_squad=role, weight=weight
            )
