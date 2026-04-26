"""MCP server + toolbox management APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.security import utcnow_naive
from app.repositories.agent import AgentRepository
from app.repositories.mcp import McpServerRepository, ToolBindingRepository, ToolboxRepository
from app.schemas.agent import AgentRead
from app.schemas.mcp import (
    AgentToolboxBindIn,
    McpServerCreate,
    McpServerHealthRead,
    McpServerRead,
    McpServerUpdate,
    ToolBindingCreate,
    ToolBindingRead,
    ToolBindingUpdate,
    ToolboxCreate,
    ToolboxRead,
    ToolboxUpdate,
)
from app.services import audit as audit_svc
from app.services import mcp as mcp_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _require_member(
    db: DBSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)


async def _require_admin(
    db: DBSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)


def _present_server(row) -> McpServerRead:
    return McpServerRead.model_validate(row)


@router.get("/servers", response_model=list[McpServerRead])
async def list_servers(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[McpServerRead]:
    ws_id = _require_workspace(workspace_id)
    await _require_member(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await McpServerRepository(db).list_for_workspace(workspace_id=ws_id)
    return [_present_server(r) for r in rows]


@router.post("/servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: McpServerCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> McpServerRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await McpServerRepository(db).create(
        workspace_id=ws_id,
        name=body.name,
        slug=body.slug,
        transport=body.transport,
        endpoint=body.endpoint,
        command=body.command,
        args_json=body.args_json,
        env_json=body.env_json,
        auth_json=body.auth_json,
        capabilities_json=body.capabilities_json,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="mcp.server.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="mcp_server",
        resource_id=row.id,
        summary=f"created mcp server {row.name!r}",
        metadata={"slug": row.slug, "transport": row.transport},
        request=request,
    )
    await db.commit()
    return _present_server(row)


@router.patch("/servers/{server_id}", response_model=McpServerRead)
async def update_server(
    server_id: uuid.UUID,
    body: McpServerUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> McpServerRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_server_or_404(db, server_id=server_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    if patch:
        row = await McpServerRepository(db).update(row, **patch)
        await audit_svc.record(
            db,
            action="mcp.server.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="mcp_server",
            resource_id=row.id,
            summary=f"updated mcp server {row.name!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
    return _present_server(row)


@router.post("/servers/{server_id}/health", response_model=McpServerHealthRead)
async def health_server(
    server_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> McpServerHealthRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_server_or_404(db, server_id=server_id, workspace_id=ws_id)
    status_value, detail = await mcp_svc.ping_server(row)
    row = await McpServerRepository(db).update(
        row, health_status=status_value, last_checked_at=utcnow_naive()
    )
    await db.commit()
    _ = row
    return McpServerHealthRead(status=status_value, detail=detail)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_server_or_404(db, server_id=server_id, workspace_id=ws_id)
    await McpServerRepository(db).soft_delete(row)
    await audit_svc.record(
        db,
        action="mcp.server.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="mcp_server",
        resource_id=row.id,
        summary=f"deleted mcp server {row.name!r}",
        request=request,
    )
    await db.commit()


@router.get("/toolboxes", response_model=list[ToolboxRead])
async def list_toolboxes(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ToolboxRead]:
    ws_id = _require_workspace(workspace_id)
    await _require_member(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await ToolboxRepository(db).list_for_workspace(workspace_id=ws_id)
    return [ToolboxRead.model_validate(r) for r in rows]


@router.post("/toolboxes", response_model=ToolboxRead, status_code=status.HTTP_201_CREATED)
async def create_toolbox(
    body: ToolboxCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ToolboxRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await ToolboxRepository(db).create(
        workspace_id=ws_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="mcp.toolbox.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="toolbox",
        resource_id=row.id,
        summary=f"created toolbox {row.name!r}",
        request=request,
    )
    await db.commit()
    return ToolboxRead.model_validate(row)


@router.patch("/toolboxes/{toolbox_id}", response_model=ToolboxRead)
async def update_toolbox(
    toolbox_id: uuid.UUID,
    body: ToolboxUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ToolboxRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_toolbox_or_404(db, toolbox_id=toolbox_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    if patch:
        row = await ToolboxRepository(db).update(row, **patch)
        await audit_svc.record(
            db,
            action="mcp.toolbox.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="toolbox",
            resource_id=row.id,
            summary=f"updated toolbox {row.name!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
    return ToolboxRead.model_validate(row)


@router.delete("/toolboxes/{toolbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_toolbox(
    toolbox_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_toolbox_or_404(db, toolbox_id=toolbox_id, workspace_id=ws_id)
    await ToolboxRepository(db).soft_delete(row)
    await audit_svc.record(
        db,
        action="mcp.toolbox.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="toolbox",
        resource_id=row.id,
        summary=f"deleted toolbox {row.name!r}",
        request=request,
    )
    await db.commit()


@router.get("/tool-bindings", response_model=list[ToolBindingRead])
async def list_tool_bindings(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    toolbox_id: uuid.UUID | None = Query(default=None),
) -> list[ToolBindingRead]:
    ws_id = _require_workspace(workspace_id)
    await _require_member(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await ToolBindingRepository(db).list_for_workspace(
        workspace_id=ws_id, toolbox_id=toolbox_id
    )
    return [ToolBindingRead.model_validate(r) for r in rows]


@router.post(
    "/tool-bindings",
    response_model=ToolBindingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tool_binding(
    body: ToolBindingCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ToolBindingRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    toolbox = await mcp_svc.get_toolbox_or_404(db, toolbox_id=body.toolbox_id, workspace_id=ws_id)
    _ = toolbox
    if body.mcp_server_id is not None:
        await mcp_svc.get_server_or_404(db, server_id=body.mcp_server_id, workspace_id=ws_id)
    row = await ToolBindingRepository(db).create(
        workspace_id=ws_id,
        toolbox_id=body.toolbox_id,
        mcp_server_id=body.mcp_server_id,
        tool_name=body.tool_name,
        alias=body.alias,
        enabled=body.enabled,
        priority=body.priority,
        config_json=body.config_json,
        metadata_json=body.metadata_json,
    )
    await audit_svc.record(
        db,
        action="mcp.tool_binding.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="tool_binding",
        resource_id=row.id,
        summary=f"bound tool {row.tool_name!r} to toolbox",
        metadata={"toolbox_id": str(row.toolbox_id)},
        request=request,
    )
    await db.commit()
    return ToolBindingRead.model_validate(row)


@router.patch("/tool-bindings/{binding_id}", response_model=ToolBindingRead)
async def update_tool_binding(
    binding_id: uuid.UUID,
    body: ToolBindingUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ToolBindingRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_binding_or_404(db, binding_id=binding_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    if "toolbox_id" in patch:
        await mcp_svc.get_toolbox_or_404(db, toolbox_id=patch["toolbox_id"], workspace_id=ws_id)
    if "mcp_server_id" in patch and patch["mcp_server_id"] is not None:
        await mcp_svc.get_server_or_404(db, server_id=patch["mcp_server_id"], workspace_id=ws_id)
    if patch:
        row = await ToolBindingRepository(db).update(row, **patch)
        await audit_svc.record(
            db,
            action="mcp.tool_binding.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="tool_binding",
            resource_id=row.id,
            summary=f"updated tool binding {row.tool_name!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
    return ToolBindingRead.model_validate(row)


@router.delete("/tool-bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool_binding(
    binding_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await mcp_svc.get_binding_or_404(db, binding_id=binding_id, workspace_id=ws_id)
    await ToolBindingRepository(db).soft_delete(row)
    await audit_svc.record(
        db,
        action="mcp.tool_binding.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="tool_binding",
        resource_id=row.id,
        summary=f"deleted tool binding {row.tool_name!r}",
        request=request,
    )
    await db.commit()


@router.post("/agents/{agent_id}/bind-toolbox", response_model=AgentRead)
async def bind_agent_toolbox(
    agent_id: uuid.UUID,
    body: AgentToolboxBindIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await _require_admin(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await AgentRepository(db).get(agent_id)
    if agent is None or agent.workspace_id != ws_id or agent.deleted_at is not None:
        raise NotFound("agent_not_found", code="agent.not_found")
    if body.toolbox_id is not None:
        await mcp_svc.get_toolbox_or_404(db, toolbox_id=body.toolbox_id, workspace_id=ws_id)
    updated = await AgentRepository(db).update(agent, toolbox_id=body.toolbox_id)
    await audit_svc.record(
        db,
        action="agent.bind_toolbox",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=updated.id,
        summary=f"bound toolbox for agent {updated.name!r}",
        metadata={"toolbox_id": str(body.toolbox_id) if body.toolbox_id else None},
        request=request,
    )
    await db.commit()
    return AgentRead.model_validate(updated)
