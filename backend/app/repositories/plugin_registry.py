"""Repository for :class:`PluginRegistry` rows (M3.9)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus
from app.db.repository import AsyncRepository


class PluginRegistryRepository(AsyncRepository[PluginRegistry]):
    model = PluginRegistry

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PluginRegistry)

    async def get_by_sha(
        self, *, name: str, version: str, sha256: str
    ) -> PluginRegistry | None:
        stmt = (
            select(PluginRegistry)
            .where(PluginRegistry.name == name)
            .where(PluginRegistry.version == version)
            .where(PluginRegistry.sha256 == sha256)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(
        self,
        *,
        status: PluginRegistryStatus | None = None,
        limit: int = 200,
    ) -> Sequence[PluginRegistry]:
        stmt = (
            select(PluginRegistry)
            .order_by(desc(PluginRegistry.updated_at))
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(PluginRegistry.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_id(self, registry_id: uuid.UUID) -> PluginRegistry | None:
        return await self.get(registry_id)

    async def upsert_discovered(
        self,
        *,
        name: str,
        version: str,
        sha256: str,
        signature: str | None,
        capability_scopes: list[str],
        folder_name: str | None,
    ) -> PluginRegistry:
        """Insert a fresh DISCOVERED row, or refresh the metadata of
        a row whose composite key already exists.

        Existing rows keep their ``approved_by_platform_admin`` flag —
        the loader must never silently re-approve a row that the admin
        already decided on. Status only advances; if a previously
        APPROVED row reappears with the same sha we leave it APPROVED
        so a reload doesn't undo the admin decision.
        """
        existing = await self.get_by_sha(
            name=name, version=version, sha256=sha256
        )
        if existing is not None:
            existing.signature = signature
            existing.capability_scopes = capability_scopes
            existing.folder_name = folder_name
            return existing

        row = PluginRegistry(
            name=name,
            version=version,
            sha256=sha256,
            signature=signature,
            capability_scopes=capability_scopes,
            folder_name=folder_name,
            status=PluginRegistryStatus.DISCOVERED,
        )
        self.session.add(row)
        await self.session.flush()
        return row
