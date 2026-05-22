"""Workspace + member + invitation routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import desc, select, text

from app.api.deps import CurrentIdentityId, DBSession
from app.core.errors import Conflict, NotFound, ServiceUnavailable, SlugTombstoned, ValidationFailed
from app.core.rate_limit import rate_limit
from app.db.models.audit import AuditEvent
from app.db.models.workspace import Workspace
from app.db.models.workspace_creation_log import CreationKind
from app.repositories.workspace import (
    InvitationRepository,
    MembershipRepository,
    WorkspaceRepository,
)
from app.schemas._base import ORMModel
from app.schemas.curator import (
    CuratorConfigIn,
    CuratorConfigOut,
    CuratorLastRunOut,
    CuratorRunResult,
)
from app.schemas.served_model import (
    ServedAliasListOut,
    ServedAliasOut,
    ServedAliasUpsertIn,
    validate_served_name,
)
from app.schemas.workspace import (
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    MemberRead,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services import audit as audit_svc
from app.services import served_model as served_svc
from app.services import workspace as svc
from app.services import workspace_quota as quota_svc
from app.services.system_settings import (
    CuratorDefaults,
    SystemSettingKey,
    get_system_setting,
)

router = APIRouter()


# ─── Workspace CRUD ──────────────────────────────────────
@router.get("", response_model=list[WorkspaceRead])
async def list_my_workspaces(db: DBSession, identity_id: CurrentIdentityId) -> list[WorkspaceRead]:
    pairs = await MembershipRepository(db).list_with_workspace_for_identity(identity_id)
    return [WorkspaceRead.model_validate(ws) for _, ws in pairs]


@router.post(
    "",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(rate_limit("workspace_create", limit=5, period_seconds=3600)),
    ],
)
async def create_workspace(
    body: WorkspaceCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> WorkspaceRead:
    """Create a workspace. Manual UI path; quota + tombstone enforced."""
    await quota_svc.check_can_create(
        db,
        identity_id=identity_id,
        creation_kind=CreationKind.MANUAL,
        request=request,
    )
    if body.slug and await quota_svc.is_slug_tombstoned(db, slug=body.slug):
        raise SlugTombstoned(
            "slug_tombstoned",
            code="workspace.slug_tombstoned",
            extras={"slug": body.slug},
        )
    ws = await svc.create_workspace(
        db,
        name=body.name,
        slug=body.slug,
        owner_identity_id=identity_id,
        description=body.description,
    )
    await quota_svc.record_creation(
        db,
        identity_id=identity_id,
        workspace_id=ws.id,
        creation_kind=CreationKind.MANUAL,
        request=request,
    )
    await db.commit()
    return WorkspaceRead.model_validate(ws)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(rate_limit("workspace_delete", limit=5, period_seconds=3600)),
    ],
)
async def delete_workspace(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> Response:
    """Soft-delete a workspace (owner-only) and tombstone its slug.

    Side effects, in order:

    1. ``workspaces.deleted_at = now()`` + ``slug_tombstoned = TRUE``
       — written via raw SQL so the workspace service stays
       untouched (M0.12 task brief contract).
    2. Every matching ``workspace_creation_logs`` row flips
       ``soft_deleted_workspace = True`` so the deleted workspace
       stops occupying the owner's quota slot.
    3. Audit rows ``workspace.deleted`` + ``workspace.quota_freed``
       so the platform admin sees both the deletion and the slot
       release in the audit feed.

    Members lose access on the next session refresh because the
    workspace service's existing reads filter ``deleted_at IS NULL``.
    """
    mem = await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    from app.db.models.role import BuiltinRole

    if mem.role != BuiltinRole.OWNER.value:
        raise Conflict(
            "owner_required_to_delete",
            code="workspace.owner_required",
        )

    result = await db.execute(
        text(
            "UPDATE workspaces "
            "SET deleted_at = now(), slug_tombstoned = TRUE, updated_at = now() "
            "WHERE id = :id AND deleted_at IS NULL"
        ),
        {"id": workspace_id},
    )
    if result.rowcount == 0:
        raise NotFound("workspace_not_found", code="workspace.not_found")

    await quota_svc.release_on_delete(
        db,
        workspace_id=workspace_id,
        actor_identity_id=identity_id,
        request=request,
    )
    await audit_svc.record(
        db,
        action="workspace.deleted",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary="workspace soft-deleted; slug tombstoned",
        metadata={"tombstoned": True},
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_id: uuid.UUID, db: DBSession, identity_id: CurrentIdentityId
) -> WorkspaceRead:
    await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is None:
        raise NotFound("workspace_not_found", code="workspace.not_found")
    return WorkspaceRead.model_validate(ws)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> WorkspaceRead:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    ws_repo = WorkspaceRepository(db)
    ws = await ws_repo.get(workspace_id)
    if ws is None:
        raise NotFound("workspace_not_found", code="workspace.not_found")
    updated = await ws_repo.update(ws, **body.model_dump(exclude_none=True))
    await db.commit()
    return WorkspaceRead.model_validate(updated)


# ─── Switch active workspace (returns a new access token) ──
class SwitchOut(ORMModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/{workspace_id}/switch", response_model=SwitchOut)
async def switch_workspace(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> SwitchOut:
    from app.core.security import create_access_token
    from app.repositories.identity import IdentityRepository

    mem = await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")
    access, _, _ = create_access_token(
        identity_id=str(identity.id),
        workspace_id=str(workspace_id),
        roles=[mem.role],
    )
    return SwitchOut(access_token=access)


# ─── Members ─────────────────────────────────────────────
@router.get("/{workspace_id}/members", response_model=list[MemberRead])
async def list_members(
    workspace_id: uuid.UUID, db: DBSession, identity_id: CurrentIdentityId
) -> list[MemberRead]:
    await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    rows = await MembershipRepository(db).list_with_identity(workspace_id=workspace_id, limit=500)
    out: list[MemberRead] = []
    for mem, ident in rows:
        card = MemberRead.model_validate(mem)
        card.identity_name = ident.name
        card.identity_email = ident.email
        card.identity_avatar_url = ident.avatar_url
        out.append(card)
    return out


class MemberPatch(ORMModel):
    role: str | None = None
    status: str | None = None
    department_id: uuid.UUID | None = None


@router.patch("/{workspace_id}/members/{identity_target}", response_model=MemberRead)
async def update_member(
    workspace_id: uuid.UUID,
    identity_target: uuid.UUID,
    body: MemberPatch,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> MemberRead:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    repo = MembershipRepository(db)
    mem = await repo.get_by_identity_and_workspace(identity_target, workspace_id)
    if mem is None:
        raise NotFound("membership_not_found", code="workspace.member_not_found")
    changes = body.model_dump(exclude_none=True)
    if changes:
        await repo.update(mem, **changes)
    await db.commit()
    return MemberRead.model_validate(mem)


@router.delete(
    "/{workspace_id}/members/{identity_target}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    workspace_id: uuid.UUID,
    identity_target: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> None:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    if identity_target == identity_id:
        raise Conflict("cannot_remove_self", code="workspace.cannot_remove_self")
    repo = MembershipRepository(db)
    mem = await repo.get_by_identity_and_workspace(identity_target, workspace_id)
    if mem is None:
        raise NotFound("membership_not_found", code="workspace.member_not_found")
    await repo.soft_delete(mem)
    await db.commit()


# ─── Invitations ─────────────────────────────────────────
@router.post(
    "/{workspace_id}/invitations",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    workspace_id: uuid.UUID,
    body: InvitationCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> InvitationRead:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    inv = await svc.create_invitation(
        db,
        workspace_id=workspace_id,
        invited_by=identity_id,
        email=body.email,
        role=body.role,
        department_id=body.department_id,
        expires_in_hours=body.expires_in_hours,
    )
    await db.commit()
    return InvitationRead.model_validate(inv)


@router.get("/{workspace_id}/invitations", response_model=list[InvitationRead])
async def list_invitations(
    workspace_id: uuid.UUID, db: DBSession, identity_id: CurrentIdentityId
) -> list[InvitationRead]:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    rows = await InvitationRepository(db).list(workspace_id=workspace_id, limit=500)
    return [InvitationRead.model_validate(r) for r in rows]


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    workspace_id: uuid.UUID,
    invitation_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> None:
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    repo = InvitationRepository(db)
    inv = await repo.get(invitation_id)
    if inv is None or inv.workspace_id != workspace_id:
        raise NotFound("invitation_not_found", code="invitation.not_found")
    await repo.hard_delete(inv)
    await db.commit()


class AcceptOut(BaseModel):
    workspace_id: uuid.UUID
    role: str


@router.post("/invitations/accept", response_model=AcceptOut)
async def accept_invitation(
    body: InvitationAccept, db: DBSession, identity_id: CurrentIdentityId
) -> AcceptOut:
    mem = await svc.accept_invitation(db, code=body.code, identity_id=identity_id)
    await db.commit()
    return AcceptOut(workspace_id=mem.workspace_id, role=mem.role)


# ─── M1.9 · Curator config (workspace settings → /settings/workspace/skills) ──
#
# Four endpoints that surface the nightly Skill Curator's per-workspace
# knobs. Resolution order: workspace ``home_config_json["curator"]`` →
# platform ``CURATOR_DEFAULTS`` → :class:`CuratorDefaults` defaults.
# Reads are member-gated, writes are admin-gated. The run-now and
# last-run endpoints depend on the M1.4 service / cron — we lazy-import
# the service so the routes still respond when M1.4 hasn't shipped yet
# (run-now → 503, last-run → empty snapshot).

_CURATOR_FIELDS: tuple[str, ...] = (
    "enabled",
    "stale_after_days",
    "archive_after_days",
    "min_idle_hours",
    "active_skills_soft_cap",
)


async def _resolve_curator_config(
    db,
    *,
    workspace_id: uuid.UUID,
) -> tuple[CuratorConfigOut, dict[str, object], dict[str, object]]:
    """Merge workspace override > platform default > schema default.

    Returns ``(config, ws_block, platform_block)`` so callers that need
    to write back (PATCH) can reuse the resolved platform values
    without re-reading the system_settings row.
    """
    raw_platform = await get_system_setting(db, SystemSettingKey.CURATOR_DEFAULTS, default={})
    platform_block: dict = raw_platform if isinstance(raw_platform, dict) else {}

    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound("workspace_not_found", code="workspace.not_found")
    ws_block_raw = (ws.home_config_json or {}).get("curator")
    ws_block: dict = ws_block_raw if isinstance(ws_block_raw, dict) else {}

    schema_defaults = CuratorDefaults().model_dump()
    merged: dict[str, object] = {}
    source: dict[str, str] = {}
    for field in _CURATOR_FIELDS:
        if field in ws_block and ws_block[field] is not None:
            merged[field] = ws_block[field]
            source[field] = "workspace"
            continue
        if field in platform_block and platform_block[field] is not None:
            merged[field] = platform_block[field]
            source[field] = "platform_default"
            continue
        merged[field] = schema_defaults[field]
        source[field] = "platform_default"

    config = CuratorConfigOut(
        enabled=bool(merged["enabled"]),
        stale_after_days=int(merged["stale_after_days"]),
        archive_after_days=int(merged["archive_after_days"]),
        min_idle_hours=int(merged["min_idle_hours"]),
        active_skills_soft_cap=int(merged["active_skills_soft_cap"]),
        source=source,  # type: ignore[arg-type]
    )
    return config, ws_block, platform_block


def _diff_curator_blocks(
    old: dict[str, object], new: dict[str, object]
) -> dict[str, dict[str, object | None]]:
    """Per-field old→new for the audit metadata. Only fields whose
    effective value changes show up; unchanged knobs stay quiet so the
    audit record is grep-able."""
    diff: dict[str, dict[str, object | None]] = {}
    for field in _CURATOR_FIELDS:
        before = old.get(field)
        after = new.get(field)
        if before != after:
            diff[field] = {"from": before, "to": after}
    return diff


@router.get(
    "/{workspace_id}/settings/curator",
    response_model=CuratorConfigOut,
    dependencies=[
        Depends(rate_limit("workspace_curator_settings_read", limit=60, period_seconds=60)),
    ],
)
async def get_curator_settings(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> CuratorConfigOut:
    """Return the merged curator config for this workspace.

    Workspace member access is enough — every member can see how
    the Curator will behave for their tenant; only admins can edit it.
    """
    await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    config, _ws_block, _platform = await _resolve_curator_config(db, workspace_id=workspace_id)
    return config


@router.patch(
    "/{workspace_id}/settings/curator",
    response_model=CuratorConfigOut,
    dependencies=[
        Depends(rate_limit("workspace_curator_settings_write", limit=20, period_seconds=60)),
    ],
)
async def update_curator_settings(
    workspace_id: uuid.UUID,
    body: CuratorConfigIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> CuratorConfigOut:
    """Persist a partial workspace override of the Curator config.

    Only fields supplied non-None are merged into
    ``workspace.home_config_json["curator"]``; passing an explicit
    ``None`` is treated as "no change", matching how the admin UI's
    "reset to platform default" button works (which sends a separate
    PATCH with the field omitted from the body — the current admin
    workflow is one knob at a time so we don't ship a bulk-reset
    verb yet).

    Cross-field invariant ``stale_after_days <= archive_after_days``
    is validated against the **effective** configuration (post-merge
    against the existing override + platform default), so an admin
    can lower one knob without supplying the other in the same PATCH
    as long as the resulting effective state still satisfies the
    invariant.
    """
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    pre_config, ws_block, _platform = await _resolve_curator_config(db, workspace_id=workspace_id)

    patch = body.model_dump(exclude_none=True)
    new_ws_block = {**ws_block, **patch}

    effective_stale = patch.get(
        "stale_after_days",
        ws_block.get("stale_after_days", pre_config.stale_after_days),
    )
    effective_archive = patch.get(
        "archive_after_days",
        ws_block.get("archive_after_days", pre_config.archive_after_days),
    )
    if int(effective_stale) > int(effective_archive):
        raise ValidationFailed(
            "stale_after_days must be less than or equal to archive_after_days",
            code="curator.stale_gt_archive",
            extras={
                "stale_after_days": int(effective_stale),
                "archive_after_days": int(effective_archive),
            },
        )

    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound("workspace_not_found", code="workspace.not_found")
    new_home = dict(ws.home_config_json or {})
    new_home["curator"] = new_ws_block
    ws.home_config_json = new_home

    post_config, _new_ws_block, _ = await _resolve_curator_config(db, workspace_id=workspace_id)

    diff = _diff_curator_blocks(
        pre_config.model_dump(exclude={"source"}),
        post_config.model_dump(exclude={"source"}),
    )
    await audit_svc.record(
        db,
        action="workspace.curator_settings_updated",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary="curator settings updated",
        metadata={"diff": diff},
        request=request,
    )
    await db.commit()
    return post_config


@router.post(
    "/{workspace_id}/settings/curator/run-now",
    response_model=CuratorRunResult,
    dependencies=[
        Depends(rate_limit("workspace_curator_run_now", limit=2, period_seconds=300)),
    ],
)
async def force_run_curator(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> CuratorRunResult:
    """Synchronously trigger one curator_tick for this workspace.

    Admin only. Rate-limited to 2 calls per 5 minutes per caller (the
    Curator is meant to be a nightly job; "run now" exists for admin
    debugging and should not be used to hand-drive the sweep).

    The actual curator_tick logic lives in :mod:`app.services.skill_curator`
    (M1.4). When that module hasn't shipped yet — common during the
    Wave-4 parallel rollout — this endpoint returns a structured 503
    so the admin UI can render a "service not ready" notice instead
    of crashing.
    """
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    try:
        from app.services import (
            skill_curator as skill_curator_svc,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        raise ServiceUnavailable(
            "Curator service is not yet available — wait for the M1.4 rollout",
            code="curator.service_not_ready",
        ) from exc

    trigger = getattr(skill_curator_svc, "trigger_curator_now", None)
    if trigger is None:
        raise ServiceUnavailable(
            "Curator service is not yet available — wait for the M1.4 rollout",
            code="curator.service_not_ready",
        )

    raw = await trigger(db=db, workspace_id=workspace_id)
    result = CuratorRunResult.model_validate(raw)

    await audit_svc.record(
        db,
        action="curator.run_now_triggered",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary="curator run-now triggered",
        metadata={
            "stale_proposed": result.stale_proposed,
            "archive_proposed": result.archive_proposed,
            "pinned_skipped": result.pinned_skipped,
            "duration_ms": result.duration_ms,
        },
        request=request,
    )
    await db.commit()
    return result


@router.get(
    "/{workspace_id}/settings/curator/last-run",
    response_model=CuratorLastRunOut,
    dependencies=[
        Depends(rate_limit("workspace_curator_history_read", limit=30, period_seconds=60)),
    ],
)
async def get_curator_last_run(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> CuratorLastRunOut:
    """Return the most recent curator_tick outcome for this workspace.

    Reads the latest ``curator.swept`` row from ``audit_events`` —
    the M1.4 Curator writes one such row per workspace per sweep. The
    metadata block carries the same shape :class:`CuratorRunResult`
    expects; rows whose metadata cannot be coerced (e.g. legacy
    pre-M1.9 sweeps) are skipped so a corrupt audit row never blanks
    the page.

    ``upcoming_run_at`` is sourced from the M1.4 Curator service
    when available; otherwise None.
    """
    await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)

    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "curator.swept",
        )
        .order_by(desc(AuditEvent.created_at))
        .limit(5)
    )
    rows = (await db.execute(stmt)).scalars().all()

    last_result: CuratorRunResult | None = None
    last_run_at = None
    for row in rows:
        meta = row.metadata_json or {}
        if not isinstance(meta, dict):
            continue
        try:
            last_result = CuratorRunResult(
                workspace_id=workspace_id,
                stale_proposed=int(meta.get("stale_proposed", 0)),
                archive_proposed=int(meta.get("archive_proposed", 0)),
                pinned_skipped=int(meta.get("pinned_skipped", 0)),
                duration_ms=int(meta.get("duration_ms", 0)),
                started_at=meta.get("started_at") or row.created_at,
                finished_at=meta.get("finished_at") or row.created_at,
            )
            last_run_at = row.created_at
            break
        except (TypeError, ValueError):
            continue

    upcoming_run_at = None
    try:
        from app.services import (
            skill_curator as skill_curator_svc,  # type: ignore[import-not-found]
        )

        next_at_fn = getattr(skill_curator_svc, "get_next_run_at", None)
        if next_at_fn is not None:
            upcoming_run_at = await next_at_fn(db, workspace_id=workspace_id)
    except ImportError:
        pass

    return CuratorLastRunOut(
        last_run_at=last_run_at,
        last_result=last_result,
        upcoming_run_at=upcoming_run_at,
    )


# ─── Served alias map (M2.5.7 Two-Model-ID Pattern) ──────
@router.get(
    "/{workspace_id}/settings/served-aliases",
    response_model=ServedAliasListOut,
    dependencies=[
        Depends(rate_limit("workspace_served_aliases_read", limit=60, period_seconds=60)),
    ],
)
async def list_served_aliases(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> ServedAliasListOut:
    await svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    alias_map = await served_svc.get_alias_map(db, workspace_id=workspace_id)
    return ServedAliasListOut(
        aliases=[ServedAliasOut(served_name=k, upstream=v) for k, v in sorted(alias_map.items())]
    )


@router.put(
    "/{workspace_id}/settings/served-aliases/{served_name}",
    response_model=ServedAliasOut,
    dependencies=[
        Depends(rate_limit("workspace_served_aliases_write", limit=20, period_seconds=60)),
    ],
)
async def upsert_served_alias(
    workspace_id: uuid.UUID,
    served_name: str,
    body: ServedAliasUpsertIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> ServedAliasOut:
    """Add / update a single served-name → upstream mapping.

    Admin only. Audit ``workspace.served_alias_upserted`` carries
    the prior upstream (if any) so an operator can reconstruct the
    sequence of upstream swaps later.
    """
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    try:
        cleaned_name = validate_served_name(served_name)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="served_alias.invalid_name") from exc
    pre_map = await served_svc.get_alias_map(db, workspace_id=workspace_id)
    prior_upstream = pre_map.get(cleaned_name)

    await served_svc.upsert_alias(
        db,
        workspace_id=workspace_id,
        served_name=cleaned_name,
        upstream=body.upstream,
    )

    await audit_svc.record(
        db,
        action="workspace.served_alias_upserted",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary=f"served alias upserted: {cleaned_name}",
        metadata={
            "served_name": cleaned_name,
            "prior_upstream": prior_upstream,
            "new_upstream": body.upstream,
        },
        request=request,
    )
    await db.commit()
    return ServedAliasOut(served_name=cleaned_name, upstream=body.upstream)


@router.delete(
    "/{workspace_id}/settings/served-aliases/{served_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(rate_limit("workspace_served_aliases_write", limit=20, period_seconds=60)),
    ],
)
async def delete_served_alias(
    workspace_id: uuid.UUID,
    served_name: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> Response:
    """Remove a served-name alias entry. Idempotent."""
    await svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    try:
        cleaned_name = validate_served_name(served_name)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="served_alias.invalid_name") from exc
    pre_map = await served_svc.get_alias_map(db, workspace_id=workspace_id)
    prior_upstream = pre_map.get(cleaned_name)

    await served_svc.delete_alias(db, workspace_id=workspace_id, served_name=cleaned_name)

    await audit_svc.record(
        db,
        action="workspace.served_alias_deleted",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary=f"served alias deleted: {cleaned_name}",
        metadata={
            "served_name": cleaned_name,
            "prior_upstream": prior_upstream,
        },
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
