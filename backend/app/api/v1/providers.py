"""Model provider routes — CRUD + vault-backed key storage."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.schemas.provider import ProviderCreate, ProviderRead, ProviderUpdate
from app.services import provider as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _attach_has_key(db, provider) -> ProviderRead:
    has_key = await svc.provider_has_key(db, provider_id=provider.id)
    out = ProviderRead.model_validate(provider)
    out.has_key = has_key
    return out


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_providers(db, workspace_id=ws_id)
    return [await _attach_has_key(db, p) for p in rows]


@router.post("", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.create_provider(
        db,
        workspace_id=ws_id,
        owner_identity_id=identity_id,
        kind=body.kind,
        name=body.name,
        base_url=body.base_url,
        default_model=body.default_model,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        api_key=body.api_key,
    )
    await db.commit()
    return await _attach_has_key(db, provider)


@router.patch("/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    await svc.update_provider(
        db,
        provider=provider,
        **body.model_dump(exclude_none=True, exclude={"api_key"}),
        api_key=body.api_key,
    )
    await db.commit()
    return await _attach_has_key(db, provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    await svc.delete_provider(db, provider=provider)
    await db.commit()
