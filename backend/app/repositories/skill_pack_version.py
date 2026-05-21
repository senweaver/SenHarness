"""Repository for :class:`~app.db.models.skill_pack_version.SkillPackVersion`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, func, select

from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.repository import AsyncRepository


class SkillPackVersionRepository(AsyncRepository[SkillPackVersion]):
    model = SkillPackVersion

    async def get_active(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
    ) -> SkillPackVersion | None:
        """Single ACTIVE row for ``pack_id`` (None if none yet)."""
        stmt = select(SkillPackVersion).where(
            SkillPackVersion.workspace_id == workspace_id,
            SkillPackVersion.pack_id == pack_id,
            SkillPackVersion.state == SkillPackVersionState.ACTIVE,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_version_no(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID, version_no: int
    ) -> SkillPackVersion | None:
        stmt = select(SkillPackVersion).where(
            SkillPackVersion.workspace_id == workspace_id,
            SkillPackVersion.pack_id == pack_id,
            SkillPackVersion.version_no == version_no,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
    ) -> SkillPackVersion | None:
        """Highest ``version_no`` row regardless of state — used for
        the ``"latest"`` label fallback in the diff endpoint."""
        stmt = (
            select(SkillPackVersion)
            .where(
                SkillPackVersion.workspace_id == workspace_id,
                SkillPackVersion.pack_id == pack_id,
            )
            .order_by(desc(SkillPackVersion.version_no))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_label(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID, label: str
    ) -> SkillPackVersion | None:
        """Resolve a string label to a concrete version row.

        Resolution order (first match wins):

        1. ``"active"`` → :meth:`get_active`
        2. ``"latest"`` → :meth:`get_latest`
        3. Pure-digit string → :meth:`get_by_version_no`
        4. Otherwise interpret the label as a version UUID and load by
           primary key, scoped to the same workspace/pack.

        Returns ``None`` when nothing matches; the caller is
        responsible for raising 404.
        """
        token = (label or "").strip()
        if not token:
            return None
        if token == "active":
            return await self.get_active(workspace_id=workspace_id, pack_id=pack_id)
        if token == "latest":
            return await self.get_latest(workspace_id=workspace_id, pack_id=pack_id)
        if token.isdigit():
            return await self.get_by_version_no(
                workspace_id=workspace_id, pack_id=pack_id, version_no=int(token)
            )
        try:
            version_uuid = uuid.UUID(token)
        except ValueError:
            return None
        stmt = select(SkillPackVersion).where(
            SkillPackVersion.id == version_uuid,
            SkillPackVersion.workspace_id == workspace_id,
            SkillPackVersion.pack_id == pack_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_pack(
        self,
        *,
        workspace_id: uuid.UUID,
        pack_id: uuid.UUID,
        limit: int = 50,
    ) -> Sequence[SkillPackVersion]:
        stmt = (
            select(SkillPackVersion)
            .where(
                SkillPackVersion.workspace_id == workspace_id,
                SkillPackVersion.pack_id == pack_id,
            )
            .order_by(desc(SkillPackVersion.version_no))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def next_version_no(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
    ) -> int:
        """One more than the current MAX(version_no) for the pack.

        Returns 1 when no rows exist. Race-safe enough for the M1.2
        write path because the unique ``(pack_id, version_no)`` index
        will reject any concurrent insert that lost the race; callers
        catch ``IntegrityError`` and retry.
        """
        stmt = select(func.max(SkillPackVersion.version_no)).where(
            SkillPackVersion.workspace_id == workspace_id,
            SkillPackVersion.pack_id == pack_id,
        )
        current = (await self.session.execute(stmt)).scalar()
        return int(current or 0) + 1

    async def find_by_hash(
        self, *, workspace_id: uuid.UUID, pack_id: uuid.UUID, content_hash: str
    ) -> SkillPackVersion | None:
        stmt = select(SkillPackVersion).where(
            SkillPackVersion.workspace_id == workspace_id,
            SkillPackVersion.pack_id == pack_id,
            SkillPackVersion.content_hash == content_hash,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
