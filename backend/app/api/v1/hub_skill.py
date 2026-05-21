"""Skill Hub catalog HTTP surface (M3.1 + M3.3).

| Method | Path                                                       | Auth                       | Bucket                        |
|--------|------------------------------------------------------------|----------------------------|-------------------------------|
| GET    | /skills/hub                                                | workspace member           | ``hub_catalog_read 60/60s``   |
| GET    | /skills/hub/{hub_pack_id}                                  | workspace member + visible | ``hub_catalog_read 60/60s``   |
| GET    | /skills/hub/{hub_pack_id}/versions                         | workspace member + visible | ``hub_catalog_read 60/60s``   |
| GET    | /skills/hub/{hub_pack_id}/versions/active                  | workspace member + visible | ``hub_catalog_read 60/60s``   |
| POST   | /admin/skills/hub/{hub_pack_id}/transition                 | platform / tenant admin    | ``hub_admin_transition 10/60s``|
| POST   | /skills/packs/{pack_id}/promote-to-hub                     | workspace admin            | ``hub_promote_initiate 5/300s``|
| POST   | /skills/hub/{hub_pack_id}/subscribe                        | workspace admin            | ``hub_subscribe 30/60s``      |
| DELETE | /skills/hub/{hub_pack_id}/subscribe                        | workspace admin            | ``hub_unsubscribe 30/60s``    |
| POST   | /skills/hub/{hub_pack_id}/pull                             | workspace admin            | ``hub_pull_manual 10/300s``   |
| GET    | /skills/hub/{hub_pack_id}/subscription-status              | workspace member           | ``hub_sub_status 60/60s``     |

Visibility rules sit in :mod:`app.services.hub_skill`. Promote /
subscribe / pull verbs sit in :mod:`app.services.hub_pull_push`. The
route layer is intentionally thin: validate, derive the workspace +
tenant context, delegate, audit, commit.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import (
    CurrentIdentityId,
    CurrentWorkspaceId,
    DBSession,
    RequireActiveIdentity,
)
from app.core.errors import HubScopePermissionDenied, NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.db.models.identity import PlatformRole
from app.repositories.hub_skill_pack import (
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.schemas.hub_skill import (
    HubPromoteRequest,
    HubPromoteResponse,
    HubPromoteSanitizationStats,
    HubPullResponse,
    HubSkillPackList,
    HubSkillPackRead,
    HubSkillPackTransitionRequest,
    HubSkillPackVersionList,
    HubSkillPackVersionRead,
    HubSkillPackVersionWithContent,
    HubSubscribeRequest,
    HubSubscriptionRead,
    HubSubscriptionStatus,
)
from app.services import hub_pull_push as hub_pp_svc
from app.services import hub_skill as hub_svc
from app.services import workspace as ws_svc

router = APIRouter(tags=["skills", "hub"])


_HUB_READ = Depends(rate_limit("hub_catalog_read", limit=60, period_seconds=60))
_HUB_ADMIN_TRANSITION = Depends(
    rate_limit("hub_admin_transition", limit=10, period_seconds=60)
)
_HUB_PROMOTE_INITIATE = Depends(
    rate_limit("hub_promote_initiate", limit=5, period_seconds=300)
)
_HUB_SUBSCRIBE = Depends(
    rate_limit("hub_subscribe", limit=30, period_seconds=60)
)
_HUB_UNSUBSCRIBE = Depends(
    rate_limit("hub_unsubscribe", limit=30, period_seconds=60)
)
_HUB_PULL_MANUAL = Depends(
    rate_limit("hub_pull_manual", limit=10, period_seconds=300)
)
_HUB_SUB_STATUS = Depends(
    rate_limit("hub_sub_status", limit=60, period_seconds=60)
)


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ── Catalog reads ───────────────────────────────────────────
@router.get(
    "/skills/hub",
    response_model=HubSkillPackList,
    dependencies=[_HUB_READ],
)
async def list_hub_catalog_route(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    scope: HubScope | None = Query(default=None),
    state: HubSkillPackState | None = Query(default=None),
    tag: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> HubSkillPackList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await hub_svc.require_hub_enabled(db)
    rows = await hub_svc.list_hub_catalog(
        db,
        workspace_id=ws_id,
        scope_filter=scope,
        state_filter=state,
        tag_filter=tag,
        limit=limit,
        offset=offset,
    )
    return HubSkillPackList(
        items=[HubSkillPackRead.model_validate(r) for r in rows]
    )


@router.get(
    "/skills/hub/{hub_pack_id}",
    response_model=HubSkillPackRead,
    dependencies=[_HUB_READ],
)
async def get_hub_pack_route(
    hub_pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> HubSkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await hub_svc.require_hub_enabled(db)
    pack = await hub_svc.get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=ws_id
    )
    return HubSkillPackRead.model_validate(pack)


@router.get(
    "/skills/hub/{hub_pack_id}/versions",
    response_model=HubSkillPackVersionList,
    dependencies=[_HUB_READ],
)
async def list_hub_pack_versions_route(
    hub_pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> HubSkillPackVersionList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await hub_svc.require_hub_enabled(db)
    rows = await hub_svc.list_hub_versions(
        db,
        hub_pack_id=hub_pack_id,
        workspace_id=ws_id,
        limit=limit,
        offset=offset,
    )
    return HubSkillPackVersionList(
        hub_pack_id=hub_pack_id,
        items=[HubSkillPackVersionRead.model_validate(r) for r in rows],
    )


@router.get(
    "/skills/hub/{hub_pack_id}/versions/active",
    response_model=HubSkillPackVersionWithContent,
    dependencies=[_HUB_READ],
)
async def get_hub_pack_active_version_route(
    hub_pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> HubSkillPackVersionWithContent:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await hub_svc.require_hub_enabled(db)
    row = await hub_svc.get_active_version(
        db, hub_pack_id=hub_pack_id, workspace_id=ws_id
    )
    return HubSkillPackVersionWithContent.model_validate(row)


# Numeric / by-version-no read is M3.3 territory (the diff verb will
# need it). We keep it parked here as a helper that the M3.3 routes
# can wire up; M3.1 only ships the literal /active route.


# ── Admin transition ────────────────────────────────────────
admin_router = APIRouter(prefix="/admin", tags=["admin", "hub"])


@admin_router.post(
    "/skills/hub/{hub_pack_id}/transition",
    response_model=HubSkillPackRead,
    status_code=status.HTTP_200_OK,
    dependencies=[_HUB_ADMIN_TRANSITION],
)
async def transition_hub_pack_route(
    hub_pack_id: uuid.UUID,
    body: HubSkillPackTransitionRequest,
    request: Request,
    db: DBSession,
    actor: RequireActiveIdentity,
    workspace_id: CurrentWorkspaceId,
) -> HubSkillPackRead:
    """Drive the hub state machine.

    PLATFORM-scope packs require ``platform_role == PLATFORM_ADMIN``;
    the service layer raises 403 if the gate fails. TENANT-scope
    packs accept any active identity that is also workspace admin
    of the resolved tenant.
    """
    await hub_svc.require_hub_enabled(db)

    # Load the pack first so we know whether to require platform
    # admin (PLATFORM scope) or tenant admin (TENANT scope).
    from app.repositories.hub_skill_pack import HubSkillPackRepository  # noqa: PLC0415

    pack = await HubSkillPackRepository(db).get(
        hub_pack_id, include_deleted=True
    )
    if pack is None:
        raise NotFound("hub_skill_pack_not_found", code="hub.pack_not_found")

    if pack.scope == HubScope.PLATFORM:
        if actor.platform_role != PlatformRole.PLATFORM_ADMIN:
            raise HubScopePermissionDenied(
                "platform_admin_required",
                code="hub.scope_permission_denied",
                extras={"scope": pack.scope.value},
            )
    else:
        # TENANT-scope: actor must be workspace admin of a workspace
        # whose resolved tenant id matches the pack's tenant id. The
        # workspace context comes from ``X-Workspace-Id``; if missing
        # we fall back to platform_admin (cross-tenant inspector).
        if actor.platform_role != PlatformRole.PLATFORM_ADMIN:
            ws_id = _require_workspace(workspace_id)
            await ws_svc.ensure_admin(
                db, workspace_id=ws_id, identity_id=actor.id
            )
            tenant_id = await hub_svc.resolve_caller_tenant(
                db, workspace_id=ws_id
            )
            if tenant_id != pack.tenant_id:
                raise HubScopePermissionDenied(
                    "cross_tenant_transition_denied",
                    code="hub.scope_permission_denied",
                    extras={
                        "scope": pack.scope.value,
                        "pack_tenant_id": (
                            str(pack.tenant_id) if pack.tenant_id else None
                        ),
                        "caller_tenant_id": (
                            str(tenant_id) if tenant_id else None
                        ),
                    },
                )

    updated = await hub_svc.transition_hub_pack_state(
        db,
        hub_pack_id=hub_pack_id,
        target_state=body.target_state,
        actor=actor,
        reason=body.reason,
        request=request,
    )
    await db.commit()
    return HubSkillPackRead.model_validate(updated)


# ── M3.3 promote / subscribe / pull verbs ───────────────────
@router.post(
    "/skills/packs/{pack_id}/promote-to-hub",
    response_model=HubPromoteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_HUB_PROMOTE_INITIATE],
)
async def promote_pack_to_hub_route(
    pack_id: uuid.UUID,
    body: HubPromoteRequest,
    request: Request,
    db: DBSession,
    actor: RequireActiveIdentity,
    workspace_id: CurrentWorkspaceId,
) -> HubPromoteResponse:
    """File a hub-promotion approval (HITL gate before the hub commit).

    Returns 202 with the new approval id + the M3.2 sanitization
    preview metadata so the admin UI can show "what would land" while
    the approver decides. The actual hub insert lands when an admin
    approves the row via the standard approvals endpoint, which fans
    through :func:`hub_pull_push.apply_promotion`.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=actor.id)

    approval = await hub_pp_svc.initiate_promotion(
        db,
        workspace_id=ws_id,
        pack_id=pack_id,
        target_scope=body.target_scope,
        actor=actor,
        target_slug=body.target_slug,
        version_id=body.version_id,
        reason=body.reason,
        request=request,
    )
    await db.commit()

    body_payload = approval.tool_args or {}
    stats_payload = body_payload.get("sanitization_stats") or {}
    return HubPromoteResponse(
        approval_id=approval.id,
        pack_id=pack_id,
        target_scope=HubScope(body_payload.get("target_scope", body.target_scope.value)),
        target_slug=str(body_payload.get("target_slug") or ""),
        target_tenant_id=(
            uuid.UUID(body_payload["target_tenant_id"])
            if body_payload.get("target_tenant_id")
            else None
        ),
        sanitized_content_hash=str(body_payload.get("sanitized_content_hash") or ""),
        sanitization_stats=HubPromoteSanitizationStats(**stats_payload),
        will_dedup_against_version_id=(
            uuid.UUID(body_payload["will_dedup_against_version_id"])
            if body_payload.get("will_dedup_against_version_id")
            else None
        ),
        will_dedup_against_pack_id=(
            uuid.UUID(body_payload["will_dedup_against_pack_id"])
            if body_payload.get("will_dedup_against_pack_id")
            else None
        ),
        expires_at=approval.expires_at,
    )


@router.post(
    "/skills/hub/{hub_pack_id}/subscribe",
    response_model=HubSubscriptionRead,
    status_code=status.HTTP_200_OK,
    dependencies=[_HUB_SUBSCRIBE],
)
async def subscribe_hub_pack_route(
    hub_pack_id: uuid.UUID,
    body: HubSubscribeRequest,
    request: Request,
    db: DBSession,
    actor: RequireActiveIdentity,
    workspace_id: CurrentWorkspaceId,
) -> HubSubscriptionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=actor.id)

    sub = await hub_pp_svc.subscribe(
        db,
        workspace_id=ws_id,
        hub_pack_id=hub_pack_id,
        auto_pull=body.auto_pull,
        actor_identity_id=actor.id,
        request=request,
    )
    await db.commit()
    return HubSubscriptionRead.model_validate(sub)


@router.delete(
    "/skills/hub/{hub_pack_id}/subscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_HUB_UNSUBSCRIBE],
)
async def unsubscribe_hub_pack_route(
    hub_pack_id: uuid.UUID,
    request: Request,
    db: DBSession,
    actor: RequireActiveIdentity,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=actor.id)

    await hub_pp_svc.unsubscribe(
        db,
        workspace_id=ws_id,
        hub_pack_id=hub_pack_id,
        actor_identity_id=actor.id,
        request=request,
    )
    await db.commit()
    return None


@router.post(
    "/skills/hub/{hub_pack_id}/pull",
    response_model=HubPullResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[_HUB_PULL_MANUAL],
)
async def pull_hub_pack_route(
    hub_pack_id: uuid.UUID,
    request: Request,
    db: DBSession,
    actor: RequireActiveIdentity,
    workspace_id: CurrentWorkspaceId,
) -> HubPullResponse:
    """Manual *Pull now* button.

    Drafts a local SkillPack(state=DRAFT) + SkillPackVersion(state=PROPOSED)
    from the hub's currently active version. The candidate still
    flows through the M2.4 verifier — the manual button does not
    bypass approval, it just collapses the wait between *hub publish*
    and *workspace candidate*.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=actor.id)

    result = await hub_pp_svc.pull_now(
        db,
        workspace_id=ws_id,
        hub_pack_id=hub_pack_id,
        actor_identity_id=actor.id,
        request=request,
    )
    await db.commit()
    return HubPullResponse(
        status=result.status,
        hub_pack_id=result.hub_pack_id,
        hub_version_no=result.hub_version_no,
        local_pack_id=result.local_pack_id,
        local_version_id=result.local_version_id,
        local_version_no=result.local_version_no,
    )


@router.get(
    "/skills/hub/{hub_pack_id}/subscription-status",
    response_model=HubSubscriptionStatus,
    dependencies=[_HUB_SUB_STATUS],
)
async def get_subscription_status_route(
    hub_pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> HubSubscriptionStatus:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await hub_svc.require_hub_enabled(db)
    # Visibility check (404 when the hub pack isn't visible to the
    # caller's tenant) — keeps subscription status from being a
    # hub-pack existence oracle for cross-tenant snoops.
    await hub_svc.get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=ws_id
    )

    sub = await WorkspaceHubSubscriptionRepository(db).get_by_pack(
        workspace_id=ws_id, hub_pack_id=hub_pack_id
    )
    active = await HubSkillPackVersionRepository(db).get_active(
        hub_pack_id=hub_pack_id
    )
    active_no = active.version_no if active is not None else None

    has_update = False
    if sub is not None and active_no is not None:
        cursor = sub.last_pulled_version_no or 0
        has_update = cursor < active_no

    return HubSubscriptionStatus(
        hub_pack_id=hub_pack_id,
        subscribed=sub is not None,
        subscription=HubSubscriptionRead.model_validate(sub) if sub else None,
        hub_active_version_no=active_no,
        has_update_available=has_update,
    )
