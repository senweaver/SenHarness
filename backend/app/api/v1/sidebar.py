"""Sidebar aggregator routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.schemas.sidebar import SidebarItemsResponse
from app.services import sidebar as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("/my-items", response_model=SidebarItemsResponse)
async def my_items(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(50, ge=1, le=100),
) -> SidebarItemsResponse:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    return await svc.list_my_items(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        limit=limit,
    )
