"""Backend Adapter CRUD — ``/api/v1/backends``.

Workspace-admin only. The plaintext X-Api-Key lives in a Vault item (cipher)
plus a SHA-256 hash column used by the gateway's hot auth path. On create or
rotation the raw key surfaces **exactly once** in the response envelope; the
UI is responsible for copying it to the worker's ``SENHARNESS_OPENCLAW_KEY``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.backend_adapter import BackendAdapterHealth
from app.repositories.backend_adapter import BackendAdapterRepository
from app.schemas.backend import (
    BackendAdapterCreate,
    BackendAdapterCreated,
    BackendAdapterHealthReport,
    BackendAdapterRead,
    BackendAdapterUpdate,
)
from app.services import audit as audit_svc
from app.services import backend_adapter as svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/backends", tags=["backends"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[BackendAdapterRead])
async def list_adapters(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[BackendAdapterRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await BackendAdapterRepository(db).list_for_workspace(workspace_id=ws_id)
    return [BackendAdapterRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=BackendAdapterCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_adapter(
    body: BackendAdapterCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BackendAdapterCreated:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)

    adapter, raw_key = await svc.create_adapter(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        name=body.name,
        kind=body.kind,
        endpoint=body.endpoint,
        metadata_json=body.metadata_json,
    )
    await audit_svc.record(
        db,
        action="backend_adapter.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="backend_adapter",
        resource_id=adapter.id,
        summary=f"created {adapter.kind.value} adapter {adapter.name!r}",
        metadata={"kind": adapter.kind.value, "endpoint": adapter.endpoint},
        request=request,
    )
    await db.commit()
    return BackendAdapterCreated(
        adapter=BackendAdapterRead.model_validate(adapter),
        api_key=raw_key,
    )


@router.get("/{adapter_id}", response_model=BackendAdapterRead)
async def get_adapter(
    adapter_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BackendAdapterRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    adapter = await svc.get_or_404(db, adapter_id=adapter_id, workspace_id=ws_id)
    return BackendAdapterRead.model_validate(adapter)


@router.patch("/{adapter_id}", response_model=BackendAdapterRead)
async def update_adapter(
    adapter_id: uuid.UUID,
    body: BackendAdapterUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BackendAdapterRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    adapter = await svc.get_or_404(db, adapter_id=adapter_id, workspace_id=ws_id)

    patch = body.model_dump(exclude_none=True)
    if patch:
        adapter = await BackendAdapterRepository(db).update(adapter, **patch)
        await audit_svc.record(
            db,
            action="backend_adapter.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="backend_adapter",
            resource_id=adapter.id,
            summary=f"updated adapter {adapter.name!r}",
            metadata=patch,
            request=request,
        )
    await db.commit()
    return BackendAdapterRead.model_validate(adapter)


@router.post(
    "/{adapter_id}/rotate-key",
    response_model=BackendAdapterCreated,
)
async def rotate_adapter_key(
    adapter_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BackendAdapterCreated:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    adapter = await svc.get_or_404(db, adapter_id=adapter_id, workspace_id=ws_id)

    adapter, raw_key = await svc.rotate_api_key(db, adapter=adapter)
    await audit_svc.record(
        db,
        action="backend_adapter.rotate_key",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="backend_adapter",
        resource_id=adapter.id,
        summary=f"rotated api key for adapter {adapter.name!r}",
        request=request,
    )
    await db.commit()
    return BackendAdapterCreated(
        adapter=BackendAdapterRead.model_validate(adapter),
        api_key=raw_key,
    )


@router.post(
    "/{adapter_id}/health",
    response_model=BackendAdapterHealthReport,
)
async def ping_adapter(
    adapter_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BackendAdapterHealthReport:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    adapter = await svc.get_or_404(db, adapter_id=adapter_id, workspace_id=ws_id)

    status_enum, detail = await svc.ping_endpoint(adapter)
    adapter = await BackendAdapterRepository(db).update(adapter, health_status=status_enum)
    await db.commit()
    _ = BackendAdapterHealth  # keep import
    return BackendAdapterHealthReport(status=status_enum, detail=detail)


@router.delete(
    "/{adapter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_adapter(
    adapter_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    adapter = await svc.get_or_404(db, adapter_id=adapter_id, workspace_id=ws_id)

    adapter_name = adapter.name
    await svc.delete_adapter(db, adapter=adapter)
    await audit_svc.record(
        db,
        action="backend_adapter.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="backend_adapter",
        resource_id=adapter.id,
        summary=f"deleted adapter {adapter_name!r}",
        request=request,
    )
    await db.commit()
