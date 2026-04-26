"""Flow CRUD + manual/webhook trigger endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.flow import FlowTriggerKind
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.schemas.flow import (
    FlowCreate,
    FlowManualTrigger,
    FlowRead,
    FlowRunRead,
    FlowUpdate,
)
from app.services import audit as audit_svc
from app.services import flow as svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/flows", tags=["flows"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[FlowRead])
async def list_flows(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[FlowRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await FlowRepository(db).list_for_workspace(workspace_id=ws_id)
    return [FlowRead.model_validate(r) for r in rows]


@router.post("", response_model=FlowRead, status_code=status.HTTP_201_CREATED)
async def create_flow(
    body: FlowCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> FlowRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.create_flow(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        **body.model_dump(),
    )
    await audit_svc.record(
        db,
        action="flow.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"created flow {flow.name!r} ({flow.trigger_kind})",
        request=request,
    )
    await db.commit()
    return FlowRead.model_validate(flow)


@router.get("/{flow_id}", response_model=FlowRead)
async def get_flow(
    flow_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> FlowRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    return FlowRead.model_validate(flow)


@router.patch("/{flow_id}", response_model=FlowRead)
async def update_flow(
    flow_id: uuid.UUID,
    body: FlowUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> FlowRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    flow = await FlowRepository(db).update(flow, **patch)
    await audit_svc.record(
        db,
        action="flow.update",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"updated flow {flow.name!r}",
        metadata={"fields": sorted(patch.keys())},
        request=request,
    )
    await db.commit()
    return FlowRead.model_validate(flow)


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(
    flow_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    await FlowRepository(db).soft_delete(flow)
    await audit_svc.record(
        db,
        action="flow.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"deleted flow {flow.name!r}",
        request=request,
    )
    await db.commit()


@router.get("/{flow_id}/runs", response_model=list[FlowRunRead])
async def list_runs(
    flow_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(50, ge=1, le=200),
) -> list[FlowRunRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    rows = await FlowRunRepository(db).list_for_flow(flow_id=flow_id, limit=limit)
    return [FlowRunRead.model_validate(r) for r in rows]


@router.post("/{flow_id}/run", response_model=FlowRunRead, status_code=status.HTTP_202_ACCEPTED)
async def trigger_manual(
    flow_id: uuid.UUID,
    body: FlowManualTrigger,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> FlowRunRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)

    run_id = await svc.trigger_flow(
        flow.id,
        workspace_id=ws_id,
        trigger_kind=FlowTriggerKind.MANUAL,
        payload=body.payload,
        triggered_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="flow.trigger",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"manual trigger of flow {flow.name!r}",
        metadata={"run_id": str(run_id), "trigger": "manual"},
        request=request,
    )
    await db.commit()
    row = await FlowRunRepository(db).get(run_id)
    return FlowRunRead.model_validate(row)
