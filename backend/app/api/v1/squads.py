"""Squad routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.repositories.squad import SquadMemberRepository
from app.schemas.squad import (
    SquadCreate,
    SquadMemberIn,
    SquadMemberRead,
    SquadRead,
    SquadReadWithMembers,
    SquadUpdate,
)
from app.services import audit as audit_svc
from app.services import squad as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[SquadRead])
async def list_squads(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SquadRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_squads(db, workspace_id=ws_id)
    return [SquadRead.model_validate(r) for r in rows]


@router.post("", response_model=SquadReadWithMembers, status_code=status.HTTP_201_CREATED)
async def create_squad(
    body: SquadCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SquadReadWithMembers:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    members = [(m.agent_id, m.role_in_squad, m.weight) for m in body.members]
    squad = await svc.create_squad(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        name=body.name,
        description=body.description,
        strategy=body.strategy,
        config_json=body.config_json,
        members=members,
    )
    await audit_svc.record(
        db,
        action="squad.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="squad",
        resource_id=squad.id,
        summary=f"created squad {squad.name!r}",
        metadata={"strategy": squad.strategy, "members": len(members)},
        request=request,
    )
    await db.commit()
    mem_rows = await SquadMemberRepository(db).list_for_squad(squad.id)
    out = SquadReadWithMembers.model_validate(squad)
    out.members = [SquadMemberRead.model_validate(m) for m in mem_rows]
    return out


@router.get("/{squad_id}", response_model=SquadReadWithMembers)
async def get_squad(
    squad_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SquadReadWithMembers:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    squad = await svc.get_or_404(db, squad_id, workspace_id=ws_id)
    mem_rows = await SquadMemberRepository(db).list_for_squad(squad.id)
    out = SquadReadWithMembers.model_validate(squad)
    out.members = [SquadMemberRead.model_validate(m) for m in mem_rows]
    return out


@router.patch("/{squad_id}", response_model=SquadReadWithMembers)
async def update_squad(
    squad_id: uuid.UUID,
    body: SquadUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SquadReadWithMembers:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    squad = await svc.get_or_404(db, squad_id, workspace_id=ws_id)
    await svc.update_squad(db, squad=squad, **body.model_dump(exclude_none=True))
    await db.commit()
    mem_rows = await SquadMemberRepository(db).list_for_squad(squad.id)
    out = SquadReadWithMembers.model_validate(squad)
    out.members = [SquadMemberRead.model_validate(m) for m in mem_rows]
    return out


@router.delete("/{squad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_squad(
    squad_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    from app.repositories.squad import SquadRepository

    squad = await svc.get_or_404(db, squad_id, workspace_id=ws_id)
    await SquadRepository(db).soft_delete(squad)
    await audit_svc.record(
        db,
        action="squad.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="squad",
        resource_id=squad.id,
        summary=f"deleted squad {squad.name!r}",
        request=request,
    )
    await db.commit()


@router.put("/{squad_id}/members", response_model=list[SquadMemberRead])
async def replace_members(
    squad_id: uuid.UUID,
    body: list[SquadMemberIn],
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SquadMemberRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_or_404(db, squad_id, workspace_id=ws_id)
    await svc.replace_members(
        db,
        squad_id=squad_id,
        members=[(m.agent_id, m.role_in_squad, m.weight) for m in body],
    )
    await db.commit()
    rows = await SquadMemberRepository(db).list_for_squad(squad_id)
    return [SquadMemberRead.model_validate(m) for m in rows]
