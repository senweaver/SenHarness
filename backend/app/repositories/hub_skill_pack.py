"""Repositories for the M3.1 Skill Hub catalog.

Three classes, all leaning on :class:`~app.db.repository.AsyncRepository`:

* :class:`HubSkillPackRepository` — catalog rows, scope/tenant-aware
  visibility helpers.
* :class:`HubSkillPackVersionRepository` — immutable bodies, dedup by
  content_hash + monotonic version_no.
* :class:`WorkspaceHubSubscriptionRepository` — workspace ↔ pack edges
  with ``auto_pull`` toggle.

Visibility model
----------------

A workspace sees a hub pack iff:

* the pack is :attr:`HubScope.PLATFORM` (any tenant), OR
* the pack is :attr:`HubScope.TENANT` AND the pack's ``tenant_id``
  equals the caller's resolved tenant id.

The tenant id is derived in :func:`app.services.hub_skill.resolve_caller_tenant`
because the deployment doesn't yet ship a dedicated tenants table —
the repository takes the resolved tenant id as input and stays
tenant-shape-agnostic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, desc, func, or_, select

from app.db.models.hub_skill_pack import HubScope, HubSkillPack, HubSkillPackState
from app.db.models.hub_skill_pack_version import HubSkillPackVersion
from app.db.models.workspace_hub_subscription import WorkspaceHubSubscription
from app.db.repository import AsyncRepository


class HubSkillPackRepository(AsyncRepository[HubSkillPack]):
    model = HubSkillPack

    async def list_visible_to_workspace(
        self,
        *,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        state: HubSkillPackState | None = None,
        tag: str | None = None,
        scope_filter: HubScope | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[HubSkillPack]:
        """Catalog rows the workspace can see.

        ``state=None`` returns ACTIVE + DEPRECATED (not ARCHIVED, not
        TOMBSTONE) — those represent the catalog's listed surface.
        Pass an explicit state to filter.
        """
        # ``workspace_id`` is intentionally accepted but unused here:
        # the visibility cut is driven by ``tenant_id``. The parameter
        # stays in the signature so future per-workspace allowlists
        # (M3.3 subscription scope filter) can land without churning
        # the call sites.
        del workspace_id

        scope_clause = or_(
            HubSkillPack.scope == HubScope.PLATFORM,
            and_(
                HubSkillPack.scope == HubScope.TENANT,
                HubSkillPack.tenant_id == tenant_id,
            ),
        )
        if scope_filter is not None:
            scope_clause = HubSkillPack.scope == scope_filter
            if scope_filter == HubScope.TENANT:
                scope_clause = and_(
                    HubSkillPack.scope == HubScope.TENANT,
                    HubSkillPack.tenant_id == tenant_id,
                )

        if state is None:
            state_clause = HubSkillPack.state.in_(
                (HubSkillPackState.ACTIVE, HubSkillPackState.DEPRECATED)
            )
        else:
            state_clause = HubSkillPack.state == state

        stmt = (
            select(HubSkillPack)
            .where(
                HubSkillPack.deleted_at.is_(None),
                scope_clause,
                state_clause,
            )
            .order_by(desc(HubSkillPack.updated_at))
            .offset(offset)
            .limit(limit)
        )
        if tag is not None:
            # JSONB containment — ``tags @> '["foo"]'`` matches any row
            # whose ``tags`` array includes ``"foo"``. Index-friendly
            # via the default GIN if one is later added; not added in
            # M3.1 because catalog volume is small for the next year.
            stmt = stmt.where(HubSkillPack.tags.op("?")(tag))

        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_id_visible(
        self,
        *,
        hub_pack_id: uuid.UUID,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
    ) -> HubSkillPack | None:
        """Single row scoped by visibility (PLATFORM or matching tenant)."""
        del workspace_id  # see ``list_visible_to_workspace``

        stmt = select(HubSkillPack).where(
            HubSkillPack.id == hub_pack_id,
            HubSkillPack.deleted_at.is_(None),
            or_(
                HubSkillPack.scope == HubScope.PLATFORM,
                and_(
                    HubSkillPack.scope == HubScope.TENANT,
                    HubSkillPack.tenant_id == tenant_id,
                ),
            ),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(
        self,
        *,
        scope: HubScope,
        tenant_id: uuid.UUID | None,
        slug: str,
    ) -> HubSkillPack | None:
        """Lookup keyed on the unique ``(scope, tenant_id, slug)``.

        Includes deleted/tombstoned rows so the slug-tombstone gate
        can find the historical row when blocking reuse.
        """
        conditions = [
            HubSkillPack.scope == scope,
            HubSkillPack.slug == slug,
        ]
        if scope == HubScope.PLATFORM:
            conditions.append(HubSkillPack.tenant_id.is_(None))
        else:
            conditions.append(HubSkillPack.tenant_id == tenant_id)

        stmt = select(HubSkillPack).where(*conditions)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_in_state(
        self,
        *,
        scope: HubScope | None = None,
        tenant_id: uuid.UUID | None = None,
        state: HubSkillPackState | None = None,
    ) -> int:
        stmt = select(func.count(HubSkillPack.id)).where(HubSkillPack.deleted_at.is_(None))
        if scope is not None:
            stmt = stmt.where(HubSkillPack.scope == scope)
        if tenant_id is not None:
            stmt = stmt.where(HubSkillPack.tenant_id == tenant_id)
        if state is not None:
            stmt = stmt.where(HubSkillPack.state == state)
        return int((await self.session.execute(stmt)).scalar() or 0)


class HubSkillPackVersionRepository(AsyncRepository[HubSkillPackVersion]):
    model = HubSkillPackVersion

    async def get_active(self, *, hub_pack_id: uuid.UUID) -> HubSkillPackVersion | None:
        stmt = select(HubSkillPackVersion).where(
            HubSkillPackVersion.hub_pack_id == hub_pack_id,
            HubSkillPackVersion.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_pack(
        self, *, hub_pack_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[HubSkillPackVersion]:
        stmt = (
            select(HubSkillPackVersion)
            .where(HubSkillPackVersion.hub_pack_id == hub_pack_id)
            .order_by(desc(HubSkillPackVersion.version_no))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def find_by_hash(
        self, *, hub_pack_id: uuid.UUID, content_hash: str
    ) -> HubSkillPackVersion | None:
        stmt = select(HubSkillPackVersion).where(
            HubSkillPackVersion.hub_pack_id == hub_pack_id,
            HubSkillPackVersion.content_hash == content_hash,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_version_no(
        self, *, hub_pack_id: uuid.UUID, version_no: int
    ) -> HubSkillPackVersion | None:
        stmt = select(HubSkillPackVersion).where(
            HubSkillPackVersion.hub_pack_id == hub_pack_id,
            HubSkillPackVersion.version_no == version_no,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def next_version_no(self, *, hub_pack_id: uuid.UUID) -> int:
        stmt = select(func.max(HubSkillPackVersion.version_no)).where(
            HubSkillPackVersion.hub_pack_id == hub_pack_id
        )
        current = (await self.session.execute(stmt)).scalar()
        return int(current or 0) + 1


class WorkspaceHubSubscriptionRepository(AsyncRepository[WorkspaceHubSubscription]):
    model = WorkspaceHubSubscription

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[WorkspaceHubSubscription]:
        stmt = (
            select(WorkspaceHubSubscription)
            .where(WorkspaceHubSubscription.workspace_id == workspace_id)
            .order_by(desc(WorkspaceHubSubscription.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_pack(
        self, *, workspace_id: uuid.UUID, hub_pack_id: uuid.UUID
    ) -> WorkspaceHubSubscription | None:
        stmt = select(WorkspaceHubSubscription).where(
            WorkspaceHubSubscription.workspace_id == workspace_id,
            WorkspaceHubSubscription.hub_pack_id == hub_pack_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_pack(
        self, *, hub_pack_id: uuid.UUID, limit: int = 500
    ) -> Sequence[WorkspaceHubSubscription]:
        stmt = (
            select(WorkspaceHubSubscription)
            .where(WorkspaceHubSubscription.hub_pack_id == hub_pack_id)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()
