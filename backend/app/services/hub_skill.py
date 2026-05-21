"""Skill Hub catalog service (M3.1).

The M3.1 surface is read-mostly: workspaces browse the catalog and
fetch metadata for a hub pack and its versions. The only mutation
endpoint at this milestone is the admin transition verb that drives
the :class:`HubSkillPackState` machine. M3.3 adds the promote / pull
verbs that actually populate the table; M3.2 inserts the privacy
sanitizer between workspace content and the hub.

Three responsibilities concentrate here:

1. **Tenant resolution.** :func:`resolve_caller_tenant` is the single
   choke point that translates a workspace id into the tenant id used
   by the visibility filter. The deployment doesn't yet ship a
   ``workspace.tenant_id`` column; we fall back to ``workspace.id``
   so a single-workspace tenant still gets the natural tenant scoping.
   When the M5+ tenancy model lands, this function changes; nothing
   else does.

2. **State machine.** :data:`ALLOWED_HUB_TRANSITIONS` defines the
   four-state lifecycle (``ACTIVE`` ↔ ``DEPRECATED`` →
   ``ARCHIVED`` → ``TOMBSTONE``). Every transition writes
   ``hub.skill_pack.transitioned`` audit; ``TOMBSTONE`` additionally
   blocks the slug from reuse inside the same ``(scope, tenant_id)``
   bucket, in line with roadmap principle 10.

3. **Settings gate.** :func:`require_hub_enabled` reads the M0.13
   ``hub`` section so the platform admin can turn the federation
   surface off entirely without removing the tables. M3.3 / M3.5
   verbs reuse the same gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    HubDisabled,
    HubInvalidStateTransition,
    HubScopePermissionDenied,
    HubSlugTombstoned,
    HubTerminalState,
    NotFound,
)
from app.core.security import utcnow_naive
from app.db.models.hub_skill_pack import HubScope, HubSkillPack, HubSkillPackState
from app.db.models.identity import Identity, PlatformRole
from app.db.models.workspace import Workspace
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.repositories.workspace import WorkspaceRepository
from app.services import audit as audit_svc
from app.services import platform_settings as platform_settings_svc

__all__ = [
    "ALLOWED_HUB_TRANSITIONS",
    "get_hub_settings",
    "is_hub_slug_tombstoned",
    "list_hub_catalog",
    "list_hub_versions",
    "list_workspace_subscriptions",
    "require_hub_enabled",
    "resolve_caller_tenant",
    "transition_hub_pack_state",
]


# ── State machine ───────────────────────────────────────────
ALLOWED_HUB_TRANSITIONS: dict[HubSkillPackState, set[HubSkillPackState]] = {
    HubSkillPackState.ACTIVE: {
        HubSkillPackState.DEPRECATED,
        HubSkillPackState.ARCHIVED,
    },
    HubSkillPackState.DEPRECATED: {
        HubSkillPackState.ACTIVE,
        HubSkillPackState.ARCHIVED,
    },
    HubSkillPackState.ARCHIVED: {
        HubSkillPackState.ACTIVE,
        HubSkillPackState.TOMBSTONE,
    },
    HubSkillPackState.TOMBSTONE: set(),
}


# ── Settings ─────────────────────────────────────────────────
async def get_hub_settings(db: AsyncSession) -> Any:
    """Fetch the M0.13 ``hub`` section (HubSettings model)."""
    return await platform_settings_svc.get_section(
        db, section=platform_settings_svc.PlatformSettingsSection.HUB
    )


async def require_hub_enabled(db: AsyncSession) -> None:
    """Raise :class:`HubDisabled` when the platform admin has flipped
    the section off. Read-cheap: backed by the platform-settings
    in-process LRU.
    """
    cfg = await get_hub_settings(db)
    if not getattr(cfg, "enabled", True):
        raise HubDisabled("hub_disabled", code="hub.disabled")


# ── Tenant resolution ────────────────────────────────────────
async def resolve_caller_tenant(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    """Translate a workspace id into the tenant id for visibility.

    Order of preference:

    1. ``workspace.tenant_id`` if the column exists (it doesn't yet).
    2. ``workspace.id`` itself — single-workspace tenant fallback.

    Returns ``None`` when the workspace can't be loaded so the caller
    can short-circuit with NotFound.
    """
    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is None:
        return None
    tenant_id = getattr(ws, "tenant_id", None)
    if tenant_id is not None:
        return tenant_id  # type: ignore[no-any-return]
    return ws.id


# ── Catalog reads ────────────────────────────────────────────
async def list_hub_catalog(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scope_filter: HubScope | None = None,
    state_filter: HubSkillPackState | None = None,
    tag_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[HubSkillPack]:
    """Visible-to-workspace catalog rows.

    Applies the M3.1 visibility cut (PLATFORM ∪ matching-tenant) and
    the optional state / scope / tag filters. Caller is responsible
    for the workspace membership check (the route does that via
    :func:`app.services.workspace.ensure_member_access`).
    """
    tenant_id = await resolve_caller_tenant(db, workspace_id=workspace_id)
    return await HubSkillPackRepository(db).list_visible_to_workspace(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        state=state_filter,
        scope_filter=scope_filter,
        tag=tag_filter,
        limit=limit,
        offset=offset,
    )


async def get_hub_pack_visible(
    db: AsyncSession,
    *,
    hub_pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> HubSkillPack:
    tenant_id = await resolve_caller_tenant(db, workspace_id=workspace_id)
    pack = await HubSkillPackRepository(db).get_by_id_visible(
        hub_pack_id=hub_pack_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
    )
    if pack is None:
        raise NotFound("hub_skill_pack_not_found", code="hub.pack_not_found")
    return pack


async def list_hub_versions(
    db: AsyncSession,
    *,
    hub_pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Any]:
    """Version history for a hub pack, scoped through the visibility
    check on the parent.
    """
    await get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=workspace_id
    )
    return await HubSkillPackVersionRepository(db).list_for_pack(
        hub_pack_id=hub_pack_id, limit=limit, offset=offset
    )


async def get_active_version(
    db: AsyncSession,
    *,
    hub_pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Any:
    await get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=workspace_id
    )
    row = await HubSkillPackVersionRepository(db).get_active(
        hub_pack_id=hub_pack_id
    )
    if row is None:
        raise NotFound(
            "hub_skill_pack_version_not_found",
            code="hub.version_not_found",
        )
    return row


async def list_workspace_subscriptions(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    limit: int = 200,
) -> Sequence[Any]:
    return await WorkspaceHubSubscriptionRepository(db).list_for_workspace(
        workspace_id=workspace_id, limit=limit
    )


# ── Slug tombstone gate ──────────────────────────────────────
async def is_hub_slug_tombstoned(
    db: AsyncSession,
    *,
    scope: HubScope,
    tenant_id: uuid.UUID | None,
    slug: str,
) -> bool:
    """Whether ``slug`` was previously tombstoned in the same bucket.

    The M3.3 promote verb consults this before allowing a new pack
    to claim the slug.
    """
    pack = await HubSkillPackRepository(db).get_by_slug(
        scope=scope, tenant_id=tenant_id, slug=slug
    )
    if pack is None:
        return False
    return pack.state == HubSkillPackState.TOMBSTONE


# ── State machine ────────────────────────────────────────────
def _check_edge(
    current: HubSkillPackState, target: HubSkillPackState
) -> None:
    if current == HubSkillPackState.TOMBSTONE:
        raise HubTerminalState(
            "hub_pack_already_tombstoned",
            code="hub.terminal_state",
            extras={"current_state": current.value},
        )
    allowed = ALLOWED_HUB_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise HubInvalidStateTransition(
            f"cannot transition {current.value} -> {target.value}",
            code="hub.invalid_transition",
            extras={
                "from": current.value,
                "to": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


def _ensure_platform_admin_for_scope(
    actor: Identity, pack: HubSkillPack
) -> None:
    """PLATFORM-scope packs may only be transitioned by a platform
    admin. TENANT-scope packs flow through the workspace admin path.
    """
    if pack.scope != HubScope.PLATFORM:
        return
    if actor.platform_role != PlatformRole.PLATFORM_ADMIN:
        raise HubScopePermissionDenied(
            "platform_admin_required",
            code="hub.scope_permission_denied",
            extras={"scope": pack.scope.value},
        )


async def transition_hub_pack_state(
    db: AsyncSession,
    *,
    hub_pack_id: uuid.UUID,
    target_state: HubSkillPackState,
    actor: Identity,
    reason: str,
    request: Any = None,
) -> HubSkillPack:
    """Move ``hub_pack_id`` to ``target_state``.

    PLATFORM-scope packs require ``actor.platform_role ==
    PLATFORM_ADMIN``; TENANT-scope packs accept any caller (the route
    layer adds the tenant-admin gate). Caller commits.
    """
    pack = await HubSkillPackRepository(db).get(
        hub_pack_id, include_deleted=True
    )
    if pack is None:
        raise NotFound("hub_skill_pack_not_found", code="hub.pack_not_found")

    _ensure_platform_admin_for_scope(actor, pack)

    current_state: HubSkillPackState = pack.state
    _check_edge(current_state, target_state)

    metadata = {
        "from": current_state.value,
        "to": target_state.value,
        "reason": reason,
        "scope": pack.scope.value,
        "tenant_id": str(pack.tenant_id) if pack.tenant_id else None,
        "slug": pack.slug,
        "hub_pack_id": str(pack.id),
    }

    pack.state = target_state
    await db.flush([pack])

    await audit_svc.record(
        db,
        action="hub.skill_pack.transitioned",
        actor_identity_id=actor.id,
        workspace_id=None,
        resource_type="hub_skill_pack",
        resource_id=pack.id,
        summary=(
            f"hub skill pack {pack.slug!r} {current_state.value} -> "
            f"{target_state.value}"
        ),
        metadata=metadata,
        request=request,
    )
    return pack


# ── Promotion helper (M3.3 entry point) ──────────────────────
async def is_caller_eligible_for_scope(
    actor: Identity, scope: HubScope
) -> bool:
    """Quick predicate the M3.3 promote verb will reuse.

    PLATFORM scope requires the platform admin role; TENANT scope is
    open to any actor (the route layer enforces tenant-admin
    membership).
    """
    if scope == HubScope.PLATFORM:
        return actor.platform_role == PlatformRole.PLATFORM_ADMIN
    return True


_ = Workspace  # Workspace is referenced indirectly via WorkspaceRepository.
