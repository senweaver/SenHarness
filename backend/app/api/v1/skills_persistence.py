"""Persistent skill-pack APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import (
    Conflict,
    NotFound,
    SkillSlugTombstoned,
    Unauthorized,
)
from app.core.rate_limit import rate_limit
from app.db.models.skill_pack_version import SkillPackVersionState
from app.db.models.skills import SkillPackState
from app.repositories.agent import AgentRepository
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import AgentSkillRepository, SkillFileRepository, SkillPackRepository
from app.schemas.skill_diff import SkillDiffRequest, SkillDiffResponse, SkillDiffStats
from app.schemas.skill_version import (
    SkillPackVersionActivateRequest,
    SkillPackVersionList,
    SkillPackVersionRead,
    SkillPackVersionTransitionRequest,
    SkillPackVersionWithContent,
    SkillRollbackRequest,
)
from app.schemas.skills_persistence import (
    AgentSkillBindIn,
    SkillPackActionReason,
    SkillPackContent,
    SkillPackCreate,
    SkillPackRead,
    SkillPackStateResponse,
    SkillPackTransitionEntry,
    SkillPackTransitionList,
    SkillPackTransitionRequest,
    SkillPackUpdate,
)
from app.services import audit as audit_svc
from app.services import skill_diff as skill_diff_svc
from app.services import skill_lifecycle as lifecycle_svc
from app.services import skill_version as skill_version_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/skills/packs", tags=["skills"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[SkillPackRead])
async def list_packs(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SkillPackRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await SkillPackRepository(db).list_for_workspace(workspace_id=ws_id)
    return [SkillPackRead.model_validate(r) for r in rows]


@router.post("", response_model=SkillPackRead, status_code=status.HTTP_201_CREATED)
async def create_pack(
    body: SkillPackCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    if await lifecycle_svc.is_slug_tombstoned(db, workspace_id=ws_id, slug=body.slug):
        raise SkillSlugTombstoned(
            "skill_pack_slug_tombstoned",
            code="skill.slug_tombstoned",
            extras={"slug": body.slug},
        )
    existing = await SkillPackRepository(db).get_by_slug(workspace_id=ws_id, slug=body.slug)
    if existing is not None:
        raise Conflict("skill_pack_slug_taken", code="skill_pack.slug_taken")

    pack = await SkillPackRepository(db).create(
        workspace_id=ws_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        version=body.version,
        publisher=body.publisher,
        signature=body.signature,
        source=body.source,
        manifest_json=body.manifest_json,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await SkillFileRepository(db).create(
        workspace_id=ws_id,
        skill_pack_id=pack.id,
        path="SKILL.md",
        content_md=body.content_md,
    )
    # Snapshot v1 inline so the version table tracks every workspace
    # pack from creation. The activate path mirrors content_hash back
    # onto the SkillPack so the M1.1 sha256(pack.id) fallback never
    # fires for newly-created rows.
    v1 = await skill_version_svc.create_version(
        db,
        workspace_id=ws_id,
        pack_id=pack.id,
        content_md=body.content_md,
        files=None,
        created_by="user",
        creator_identity_id=identity_id,
        request=request,
    )
    await skill_version_svc.activate_version(
        db,
        workspace_id=ws_id,
        version_id=v1.id,
        actor_identity_id=identity_id,
        reason="initial version",
        request=request,
    )
    await audit_svc.record(
        db,
        action="skill_pack.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=f"created skill pack {pack.slug!r}",
        metadata={"version": pack.version},
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.get("/{pack_id}", response_model=SkillPackRead)
async def get_pack(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await SkillPackRepository(db).get(pack_id)
    if row is None or row.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return SkillPackRead.model_validate(row)


@router.get("/{pack_id}/content", response_model=SkillPackContent)
async def get_pack_content(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackContent:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await SkillPackRepository(db).get(pack_id)
    if row is None or row.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    files = await SkillFileRepository(db).list_for_pack(workspace_id=ws_id, skill_pack_id=pack_id)
    content = "\n\n".join([f.content_md for f in files if f.path.endswith("SKILL.md")]) or ""
    return SkillPackContent(pack=SkillPackRead.model_validate(row), content_md=content)


@router.patch("/{pack_id}", response_model=SkillPackRead)
async def update_pack(
    pack_id: uuid.UUID,
    body: SkillPackUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillPackRead:
    """Update SkillPack metadata.

    M1.2 contract: a body that carries ``content_md`` or ``files_json``
    is routed through :mod:`app.services.skill_version` — a new
    immutable :class:`~app.db.models.skill_pack_version.SkillPackVersion`
    is snapshotted and activated, which mirrors the new bytes back onto
    the pack's cache columns. Direct in-place rewrites of
    ``SkillPack.content_md`` are no longer permitted via the API; the
    old code path is gone. Other metadata (name / description / tags /
    enabled / etc.) still updates the SkillPack row directly.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await SkillPackRepository(db).get(pack_id)
    if row is None or row.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")

    payload = body.model_dump(exclude_none=True)
    new_content_md: str | None = payload.pop("content_md", None)
    new_files_json: dict[str, str] | None = payload.pop("files_json", None)

    new_version = None
    if new_content_md is not None or new_files_json is not None:
        # The hash is computed over (body, files) so omitting either
        # side falls back to the active version's value — that way a
        # caller patching just ``files_json`` still gets a hash that
        # reflects the body too.
        if new_content_md is None:
            file_repo = SkillFileRepository(db)
            files = await file_repo.list_for_pack(
                workspace_id=ws_id, skill_pack_id=row.id
            )
            skill_md = next((f for f in files if f.path == "SKILL.md"), None)
            new_content_md = skill_md.content_md if skill_md else ""
        try:
            new_version = await skill_version_svc.create_version(
                db,
                workspace_id=ws_id,
                pack_id=row.id,
                content_md=new_content_md,
                files=new_files_json,
                created_by="user",
                creator_identity_id=identity_id,
                request=request,
            )
        except skill_version_svc.SkillPackVersionConflict:
            # Identical content already exists — the API still wants to
            # apply the metadata patch (if any) and report success on
            # the existing pack. Fall through with no new version.
            new_version = None
        else:
            await skill_version_svc.activate_version(
                db,
                workspace_id=ws_id,
                version_id=new_version.id,
                actor_identity_id=identity_id,
                reason="user edit",
                request=request,
            )
            await db.refresh(row)

    if payload:
        row = await SkillPackRepository(db).update(row, **payload)

    if payload or new_version is not None:
        meta: dict = {"fields": sorted(list(payload.keys()))}
        if new_version is not None:
            meta["new_version_id"] = str(new_version.id)
            meta["new_version_no"] = new_version.version_no
        await audit_svc.record(
            db,
            action="skill_pack.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="skill_pack",
            resource_id=row.id,
            summary=f"updated skill pack {row.slug!r}",
            metadata=meta,
            request=request,
        )
    await db.commit()
    await db.refresh(row)
    return SkillPackRead.model_validate(row)


@router.delete("/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pack(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await SkillPackRepository(db).get(pack_id)
    if row is None or row.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    await SkillPackRepository(db).soft_delete(row)
    await audit_svc.record(
        db,
        action="skill_pack.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="skill_pack",
        resource_id=row.id,
        summary=f"deleted skill pack {row.slug!r}",
        request=request,
    )
    await db.commit()


# ── M1.1 lifecycle endpoints ────────────────────────────────
_LIFECYCLE_ACTION = Depends(rate_limit("skill_lifecycle_action", limit=30, period_seconds=60))
_LIFECYCLE_ADMIN_ACTION = Depends(
    rate_limit("skill_lifecycle_admin_action", limit=20, period_seconds=60)
)
_LIFECYCLE_READ = Depends(rate_limit("skill_lifecycle_read", limit=60, period_seconds=60))


def _state_response(pack, last: SkillPackTransitionEntry | None) -> SkillPackStateResponse:
    return SkillPackStateResponse(
        pack_id=pack.id,
        state=pack.state,
        pinned=pack.pinned,
        state_changed_at=pack.state_changed_at,
        state_changed_by=pack.state_changed_by,
        last_transition=last,
    )


@router.post(
    "/{pack_id}/pin",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ACTION],
)
async def pin_pack_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackActionReason | None = None,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    reason = (body.reason if body else None) or "user pinned"
    pack = await lifecycle_svc.pin_pack(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        actor_identity_id=identity_id,
        reason=reason,
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.post(
    "/{pack_id}/unpin",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ACTION],
)
async def unpin_pack_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackActionReason | None = None,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    reason = (body.reason if body else None) or "user unpinned"
    pack = await lifecycle_svc.unpin_pack(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        actor_identity_id=identity_id,
        reason=reason,
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.post(
    "/{pack_id}/archive",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ACTION],
)
async def archive_pack_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackActionReason | None = None,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    reason = (body.reason if body else None) or "user archived"
    pack = await lifecycle_svc.transition(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        target_state=SkillPackState.ARCHIVED,
        actor_identity_id=identity_id,
        reason=reason,
        bypass_pinned=True,
        actor_kind="user",
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.post(
    "/{pack_id}/restore",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ACTION],
)
async def restore_pack_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackActionReason | None = None,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    reason = (body.reason if body else None) or "user restored"
    pack = await lifecycle_svc.transition(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        target_state=SkillPackState.ACTIVE,
        actor_identity_id=identity_id,
        reason=reason,
        bypass_pinned=True,
        actor_kind="user",
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.post(
    "/{pack_id}/deprecate",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ACTION],
)
async def deprecate_pack_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackActionReason | None = None,
) -> SkillPackRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    reason = (body.reason if body else None) or "user deprecated"
    pack = await lifecycle_svc.transition(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        target_state=SkillPackState.DEPRECATED,
        actor_identity_id=identity_id,
        reason=reason,
        bypass_pinned=True,
        actor_kind="user",
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.post(
    "/{pack_id}/transitions",
    response_model=SkillPackRead,
    dependencies=[_LIFECYCLE_ADMIN_ACTION],
)
async def transition_pack_route(
    pack_id: uuid.UUID,
    body: SkillPackTransitionRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillPackRead:
    """Generic transition (admin-only). Forces the pinned bypass so an
    admin can always move a pack out of an unexpected state — pinned
    *protects from auto-flow*, not from explicit admin override.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await lifecycle_svc.transition(
        db,
        pack_id=pack_id,
        workspace_id=ws_id,
        target_state=body.target_state,
        actor_identity_id=identity_id,
        reason=body.reason,
        bypass_pinned=True,
        actor_kind="user",
        request=request,
    )
    await db.commit()
    await db.refresh(pack)
    return SkillPackRead.model_validate(pack)


@router.get(
    "/{pack_id}/state",
    response_model=SkillPackStateResponse,
    dependencies=[_LIFECYCLE_READ],
)
async def get_pack_state_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackStateResponse:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    history = await lifecycle_svc.list_transitions(db, workspace_id=ws_id, pack_id=pack_id, limit=1)
    last = SkillPackTransitionEntry.model_validate(history[0]) if history else None
    return _state_response(pack, last)


@router.get(
    "/{pack_id}/transitions",
    response_model=SkillPackTransitionList,
    dependencies=[_LIFECYCLE_READ],
)
async def list_pack_transitions_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackTransitionList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    history = await lifecycle_svc.list_transitions(
        db, workspace_id=ws_id, pack_id=pack_id, limit=200
    )
    items = [SkillPackTransitionEntry.model_validate(h) for h in history]
    return SkillPackTransitionList(pack_id=pack_id, items=items)


@router.post("/agents/{agent_id}/bind", status_code=status.HTTP_200_OK)
async def bind_agent_skills(
    agent_id: uuid.UUID,
    body: AgentSkillBindIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await AgentRepository(db).get(agent_id)
    if agent is None or agent.workspace_id != ws_id:
        raise NotFound("agent_not_found", code="agent.not_found")

    existing = await AgentSkillRepository(db).list_for_agent(workspace_id=ws_id, agent_id=agent_id)
    existing_ids = {e.skill_pack_id: e for e in existing}
    target_ids = set(body.skill_pack_ids)

    for skill_pack_id, row in existing_ids.items():
        if skill_pack_id not in target_ids:
            await AgentSkillRepository(db).soft_delete(row)

    for skill_pack_id in target_ids:
        if skill_pack_id in existing_ids:
            continue
        pack = await SkillPackRepository(db).get(skill_pack_id)
        if pack is None or pack.workspace_id != ws_id:
            raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
        await AgentSkillRepository(db).create(
            workspace_id=ws_id,
            agent_id=agent_id,
            skill_pack_id=skill_pack_id,
            enabled=True,
        )

    patch_meta = dict(agent.metadata_json or {})
    patch_meta["skills"] = [str(sid) for sid in sorted(target_ids, key=str)]
    await AgentRepository(db).update(agent, metadata_json=patch_meta)

    await audit_svc.record(
        db,
        action="agent.bind_skills",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent_id,
        summary=f"bound {len(target_ids)} skill packs to agent",
        metadata={"skill_pack_ids": [str(i) for i in sorted(target_ids, key=str)]},
        request=request,
    )
    await db.commit()
    return {"agent_id": str(agent_id), "bound_count": len(target_ids)}


# ── M1.2 SkillPackVersion endpoints ─────────────────────────
_VERSION_READ = Depends(rate_limit("skill_version_read", limit=60, period_seconds=60))
_VERSION_WRITE = Depends(rate_limit("skill_version_write", limit=20, period_seconds=60))


async def _ensure_pack_in_workspace(
    db, *, ws_id: uuid.UUID, pack_id: uuid.UUID
):
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return pack


@router.get(
    "/{pack_id}/versions",
    response_model=SkillPackVersionList,
    dependencies=[_VERSION_READ],
)
async def list_pack_versions_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackVersionList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    rows = await SkillPackVersionRepository(db).list_for_pack(
        workspace_id=ws_id, pack_id=pack_id, limit=100
    )
    return SkillPackVersionList(
        pack_id=pack_id,
        items=[SkillPackVersionRead.model_validate(r) for r in rows],
    )


@router.get(
    "/{pack_id}/versions/active",
    response_model=SkillPackVersionWithContent,
    dependencies=[_VERSION_READ],
)
async def get_active_version_route(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackVersionWithContent:
    """Returns the body of the currently-ACTIVE snapshot.

    Routed under ``/versions/active`` (a literal segment) instead of
    ``/versions/{version_no}`` so the route resolver sees ``active``
    *before* the integer-typed numeric route below — FastAPI uses
    declaration order for ambiguous matches and we want the literal
    to win regardless of the path-converter.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    row = await SkillPackVersionRepository(db).get_active(
        workspace_id=ws_id, pack_id=pack_id
    )
    if row is None:
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )
    return SkillPackVersionWithContent.model_validate(row)


@router.get(
    "/{pack_id}/versions/{version_no}",
    response_model=SkillPackVersionWithContent,
    dependencies=[_VERSION_READ],
)
async def get_pack_version_route(
    pack_id: uuid.UUID,
    version_no: int,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillPackVersionWithContent:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    row = await SkillPackVersionRepository(db).get_by_version_no(
        workspace_id=ws_id, pack_id=pack_id, version_no=version_no
    )
    if row is None:
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )
    return SkillPackVersionWithContent.model_validate(row)


@router.post(
    "/{pack_id}/versions/{version_id}/activate",
    response_model=SkillPackVersionRead,
    dependencies=[_VERSION_WRITE],
)
async def activate_pack_version_route(
    pack_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    body: SkillPackVersionActivateRequest | None = None,
) -> SkillPackVersionRead:
    """Promote ``version_id`` to ACTIVE; retire previous ACTIVE.

    Workspace admin only — flipping the ACTIVE version is what runtime
    skill injection reads, so it has the same blast radius as a manual
    edit.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    target = await SkillPackVersionRepository(db).get(version_id)
    if target is None or target.workspace_id != ws_id or target.pack_id != pack.id:
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )

    reason = (body.reason if body else None) or "admin activated"
    activated = await skill_version_svc.activate_version(
        db,
        workspace_id=ws_id,
        version_id=version_id,
        actor_identity_id=identity_id,
        reason=reason,
        request=request,
    )
    await db.commit()
    return SkillPackVersionRead.model_validate(activated)


@router.post(
    "/{pack_id}/versions/{version_id}/transition",
    response_model=SkillPackVersionRead,
    dependencies=[_VERSION_WRITE],
)
async def transition_pack_version_route(
    pack_id: uuid.UUID,
    version_id: uuid.UUID,
    body: SkillPackVersionTransitionRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillPackVersionRead:
    """Drive the version state machine.

    Allowed edges: PROPOSED → VALIDATING / REJECTED, VALIDATING →
    ACCEPTED / REJECTED, ACCEPTED → ACTIVE, ACTIVE → RETIRED. Moving
    to ACTIVE delegates to the activate path so the previous
    incumbent is retired in the same transaction.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    target = await SkillPackVersionRepository(db).get(version_id)
    if target is None or target.workspace_id != ws_id or target.pack_id != pack.id:
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )

    updated = await skill_version_svc.transition_version(
        db,
        workspace_id=ws_id,
        version_id=version_id,
        target_state=body.target_state,
        actor_identity_id=identity_id,
        reason=body.reason,
        request=request,
    )
    await db.commit()
    return SkillPackVersionRead.model_validate(updated)


# ─── M1.10 — Skill diff renderer ─────────────────────────────
# Separate router so the M1.1 lifecycle additions to ``router`` above
# stay clean of any diff concerns. Both routers are mounted in
# ``app.api.router``.

diff_router = APIRouter(prefix="/skills", tags=["skills"])


@diff_router.post(
    "/diff",
    response_model=SkillDiffResponse,
    dependencies=[Depends(rate_limit("skill_diff_compute", 30, 60))],
)
async def compute_skill_diff(
    body: SkillDiffRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillDiffResponse:
    """Render a unified diff for any two pieces of skill content.

    Workspace-member only. Per-side cap is enforced by pydantic;
    truncation only applies to the *response* diff text so the wire
    payload stays bounded even when the inputs are at the upper limit.
    """
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)

    result = skill_diff_svc.render_unified_diff(
        body.old_content,
        body.new_content,
        context_lines=body.context_lines,
        file_label=body.file_label,
        from_label=body.from_label,
        to_label=body.to_label,
    )
    display_diff, was_truncated = skill_diff_svc.truncate_diff_for_display(result.diff)

    if was_truncated:
        await audit_svc.record(
            db,
            action="skill.diff_truncated",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=None,
            summary="skill diff exceeded display caps",
            metadata={
                "added_lines": result.stats.added_lines,
                "removed_lines": result.stats.removed_lines,
                "hunks": result.stats.hunks,
                "file_label": body.file_label,
            },
            request=request,
        )
        await db.commit()

    return SkillDiffResponse(
        diff=display_diff,
        stats=SkillDiffStats(**result.stats.as_dict()),
        files_changed=result.files_changed,
        truncated=was_truncated,
    )


@diff_router.get(
    "/packs/{pack_id}/versions/{version_a}/diff/{version_b}",
    response_model=SkillDiffResponse,
    dependencies=[Depends(rate_limit("skill_diff_compute", 30, 60))],
)
async def get_skill_version_diff(
    pack_id: uuid.UUID,
    version_a: str,
    version_b: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillDiffResponse:
    """Diff two persisted SkillPack versions.

    Each label is resolved through
    :meth:`SkillPackVersionRepository.get_by_label` which accepts
    ``"active"``, ``"latest"``, a numeric string interpreted as
    ``version_no``, or a UUID interpreted as ``id``. Both labels must
    resolve to a row scoped to the same workspace and pack — a missing
    label or a cross-workspace lookup returns 404 with code
    ``skill_version.not_found``.
    """
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)

    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")

    repo = SkillPackVersionRepository(db)
    ver_a = await repo.get_by_label(
        workspace_id=workspace_id, pack_id=pack_id, label=version_a
    )
    ver_b = await repo.get_by_label(
        workspace_id=workspace_id, pack_id=pack_id, label=version_b
    )
    if ver_a is None or ver_b is None:
        raise NotFound(
            "skill_pack_version_not_found",
            code="skill_version.not_found",
            extras={
                "version_a": version_a,
                "version_b": version_b,
                "resolved_a": str(ver_a.id) if ver_a else None,
                "resolved_b": str(ver_b.id) if ver_b else None,
            },
        )

    result = skill_diff_svc.render_unified_diff(
        ver_a.content_md,
        ver_b.content_md,
        file_label=f"{pack.slug}/SKILL.md",
        from_label=f"v{ver_a.version_no}",
        to_label=f"v{ver_b.version_no}",
    )
    display_diff, was_truncated = skill_diff_svc.truncate_diff_for_display(result.diff)

    if was_truncated:
        await audit_svc.record(
            db,
            action="skill.diff_truncated",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=pack_id,
            summary="skill diff exceeded display caps",
            metadata={
                "added_lines": result.stats.added_lines,
                "removed_lines": result.stats.removed_lines,
                "hunks": result.stats.hunks,
                "version_a": ver_a.version_no,
                "version_b": ver_b.version_no,
            },
            request=request,
        )
        await db.commit()

    return SkillDiffResponse(
        diff=display_diff,
        stats=SkillDiffStats(**result.stats.as_dict()),
        files_changed=result.files_changed,
        truncated=was_truncated,
    )


# ─── M1.6 — Rollback verb endpoint ───────────────────────────
# Service-layer ``rollback_to_version`` already lives in
# :mod:`app.services.skill_version` (M1.2). This endpoint is the only
# new wire surface M1.6 introduces: an admin-only verb that promotes
# a historical SkillPackVersion to ACTIVE and writes the dedicated
# ``skill_version.rollback`` audit row on top of the
# activate/retire pair the service already emits. Tighter rate
# bucket (10/60s) than the generic version-write quota (20/60s) —
# rollback is a rarer, higher-blast-radius verb so the slower budget
# also acts as a sanity gate against accidental loops in admin UIs.


@router.post(
    "/{pack_id}/versions/{version_id}/rollback",
    response_model=SkillPackVersionRead,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("skill_version_rollback", limit=10, period_seconds=60))
    ],
)
async def rollback_to_version_endpoint(
    pack_id: uuid.UUID,
    version_id: uuid.UUID,
    body: SkillRollbackRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillPackVersionRead:
    """Re-promote a historical SkillPackVersion to ACTIVE.

    Atomic via the M1.2 service: the previous ACTIVE row is RETIRED,
    the target row becomes ACTIVE, and the SkillPack cache columns
    (``content_md`` / ``content_hash``) are mirrored back in the same
    transaction. Workspace admin only — flipping which snapshot is
    live has the same blast radius as a manual edit. The service
    raises ``SkillPackVersionTransitionError`` (409) if the target is
    in REJECTED state, since once a version is nuked it can't be
    revived without a fresh proposal. Rolling back to the row that's
    already ACTIVE is idempotent and returns 200 — the
    ``_retire_current_active`` no-op short-circuits the retire side.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    pack = await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    target = await SkillPackVersionRepository(db).get(version_id)
    if (
        target is None
        or target.workspace_id != ws_id
        or target.pack_id != pack.id
    ):
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )

    activated = await skill_version_svc.rollback_to_version(
        db,
        workspace_id=ws_id,
        pack_id=pack.id,
        target_version_id=version_id,
        actor_identity_id=identity_id,
        reason=body.reason,
        request=request,
    )
    await db.commit()
    await db.refresh(activated)
    return SkillPackVersionRead.model_validate(activated)
