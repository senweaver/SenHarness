"""Repositories for persistent skill packs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select

from app.db.models.skills import AgentSkill, SkillFile, SkillPack
from app.db.repository import AsyncRepository


class SkillPackRepository(AsyncRepository[SkillPack]):
    model = SkillPack

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[SkillPack]:
        stmt = (
            select(SkillPack)
            .where(SkillPack.workspace_id == workspace_id, SkillPack.deleted_at.is_(None))
            .order_by(desc(SkillPack.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_slug(self, *, workspace_id: uuid.UUID, slug: str) -> SkillPack | None:
        stmt = select(SkillPack).where(
            SkillPack.workspace_id == workspace_id,
            SkillPack.slug == slug,
            SkillPack.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SkillFileRepository(AsyncRepository[SkillFile]):
    model = SkillFile

    async def list_for_pack(
        self, *, workspace_id: uuid.UUID, skill_pack_id: uuid.UUID
    ) -> Sequence[SkillFile]:
        stmt = (
            select(SkillFile)
            .where(
                SkillFile.workspace_id == workspace_id,
                SkillFile.skill_pack_id == skill_pack_id,
                SkillFile.deleted_at.is_(None),
            )
            .order_by(SkillFile.path.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()


class AgentSkillRepository(AsyncRepository[AgentSkill]):
    model = AgentSkill

    async def list_for_agent(
        self, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Sequence[AgentSkill]:
        stmt = select(AgentSkill).where(
            AgentSkill.workspace_id == workspace_id,
            AgentSkill.agent_id == agent_id,
            AgentSkill.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalars().all()
