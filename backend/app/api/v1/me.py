"""Current identity endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import MembershipRepository
from app.schemas.identity import (
    IdentityRead,
    IdentityUpdate,
    MembershipBrief,
    MeOut,
    PasswordChangeIn,
)
from app.services import auth as auth_svc
from app.services import permissions as perm

router = APIRouter()


@router.get("", response_model=MeOut)
async def read_me(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MeOut:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")

    pairs = await MembershipRepository(db).list_with_workspace_for_identity(identity_id)
    memberships = [
        MembershipBrief(
            workspace_id=ws.id,
            workspace_name=ws.name,
            workspace_slug=ws.slug,
            role=mem.role,
            department_id=mem.department_id,
        )
        for mem, ws in pairs
    ]

    out = MeOut.model_validate(identity)
    out.workspaces = memberships
    out.current_workspace_id = workspace_id or (
        memberships[0].workspace_id if memberships else None
    )

    # Surface role + permissions of the active workspace. Falls back to the
    # first membership when no specific workspace is requested.
    active_ws = out.current_workspace_id
    if active_ws is not None:
        active_mem = next(
            (m for m, _ws in pairs if m.workspace_id == active_ws), None
        )
        if active_mem is not None:
            out.current_role = active_mem.role
            out.current_department_id = active_mem.department_id
            out.permissions = sorted(perm.capabilities_for(active_mem.role))
    return out


@router.patch("", response_model=IdentityRead)
async def update_me(
    body: IdentityUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> IdentityRead:
    repo = IdentityRepository(db)
    identity = await repo.get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")
    updated = await repo.update(identity, **{k: v for k, v in body.model_dump(exclude_none=True).items()})
    await db.commit()
    return IdentityRead.model_validate(updated)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> None:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")
    await auth_svc.change_password(
        db, identity=identity, old_password=body.old_password, new_password=body.new_password
    )
    await db.commit()
