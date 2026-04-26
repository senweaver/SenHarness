"""Workspace + member + invitation routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.api.deps import CurrentIdentityId, DBSession
from app.core.errors import Conflict, NotFound
from app.repositories.workspace import (
    InvitationRepository,
    MembershipRepository,
    WorkspaceRepository,
)
from app.schemas._base import ORMModel
from app.schemas.workspace import (
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    MemberRead,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services import workspace as svc

router = APIRouter()


# ─── Workspace CRUD ──────────────────────────────────────
@router.get("", response_model=list[WorkspaceRead])
async def list_my_workspaces(
    db: DBSession, identity_id: CurrentIdentityId
) -> list[WorkspaceRead]:
    pairs = await MembershipRepository(db).list_with_workspace_for_identity(identity_id)
    return [WorkspaceRead.model_validate(ws) for _, ws in pairs]


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, db: DBSession, identity_id: CurrentIdentityId
) -> WorkspaceRead:
    ws = await svc.create_workspace(
        db,
        name=body.name,
        slug=body.slug,
        owner_identity_id=identity_id,
        description=body.description,
    )
    await db.commit()
    return WorkspaceRead.model_validate(ws)


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

    mem = await svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
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
    rows = await MembershipRepository(db).list_with_identity(
        workspace_id=workspace_id, limit=500
    )
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
