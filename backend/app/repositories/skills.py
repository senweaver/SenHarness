"""Repositories for persistent skill packs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, or_, select

from app.db.models.skills import AgentSkill, SkillFile, SkillPack, SkillPackState
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

    # ── M1.1 lifecycle helpers ────────────────────────────────
    async def list_by_state(
        self,
        *,
        workspace_id: uuid.UUID,
        state: SkillPackState | None = None,
        limit: int = 200,
    ) -> Sequence[SkillPack]:
        """List packs filtered by state. ``state=None`` returns all
        non-tombstoned, non-soft-deleted rows for the workspace.
        """
        stmt = select(SkillPack).where(
            SkillPack.workspace_id == workspace_id,
            SkillPack.deleted_at.is_(None),
        )
        if state is None:
            stmt = stmt.where(SkillPack.state != SkillPackState.TOMBSTONE)
        else:
            stmt = stmt.where(SkillPack.state == state)
        stmt = stmt.order_by(desc(SkillPack.updated_at)).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_active(
        self,
        *,
        workspace_id: uuid.UUID,
        ids: list[uuid.UUID] | None = None,
        limit: int = 500,
    ) -> Sequence[SkillPack]:
        """Packs eligible for runtime injection (M1.7 ``build_skills_capability``).

        Eligibility: ``state == ACTIVE`` OR (``pinned`` AND
        ``state != TOMBSTONE``). Pinned packs in STALE/DEPRECATED still
        win because the user explicitly opted in to keeping them
        available — auto sweeps cannot have moved them past PIN.
        """
        stmt = select(SkillPack).where(
            SkillPack.workspace_id == workspace_id,
            SkillPack.deleted_at.is_(None),
            SkillPack.state != SkillPackState.TOMBSTONE,
            or_(
                SkillPack.state == SkillPackState.ACTIVE,
                SkillPack.pinned.is_(True),
            ),
        )
        if ids is not None:
            stmt = stmt.where(SkillPack.id.in_(ids))
        stmt = stmt.order_by(desc(SkillPack.updated_at)).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_pinned(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[SkillPack]:
        stmt = (
            select(SkillPack)
            .where(
                SkillPack.workspace_id == workspace_id,
                SkillPack.deleted_at.is_(None),
                SkillPack.pinned.is_(True),
                SkillPack.state != SkillPackState.TOMBSTONE,
            )
            .order_by(desc(SkillPack.updated_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


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
