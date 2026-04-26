"""Persistent skill-pack APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Conflict, NotFound, Unauthorized
from app.repositories.agent import AgentRepository
from app.repositories.skills import AgentSkillRepository, SkillFileRepository, SkillPackRepository
from app.schemas.skills_persistence import (
    AgentSkillBindIn,
    SkillPackContent,
    SkillPackCreate,
    SkillPackRead,
    SkillPackUpdate,
)
from app.services import audit as audit_svc
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
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await SkillPackRepository(db).get(pack_id)
    if row is None or row.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    patch = body.model_dump(exclude_none=True)
    if patch:
        row = await SkillPackRepository(db).update(row, **patch)
        await audit_svc.record(
            db,
            action="skill_pack.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="skill_pack",
            resource_id=row.id,
            summary=f"updated skill pack {row.slug!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
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

    # Soft-delete removed bindings.
    for skill_pack_id, row in existing_ids.items():
        if skill_pack_id not in target_ids:
            await AgentSkillRepository(db).soft_delete(row)

    # Add missing bindings.
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

    # Keep legacy metadata_json.skills synchronized for runtime compatibility.
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
