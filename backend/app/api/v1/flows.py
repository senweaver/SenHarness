"""Flow CRUD + manual/webhook trigger endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized, ValidationFailed
from app.core.rate_limit import rate_limit
from app.db.models.flow import FlowExecutionMode, FlowTriggerKind
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.schemas.flow import (
    FlowCreate,
    FlowManualTrigger,
    FlowRead,
    FlowRunRead,
    FlowTestResult,
    FlowUpdate,
    HttpModeConfig,
    ScriptModeConfig,
)
from app.services import audit as audit_svc
from app.services import flow as svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/flows", tags=["flows"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _validate_merged_no_agent_config(
    *, execution_mode: FlowExecutionMode, trigger_config: dict
) -> None:
    """Re-validate the post-merge state when PATCH only touches one field.

    The Pydantic-level validators on ``FlowUpdate`` skip cross-field checks
    for partial updates; the caller of this helper has the merged dict in
    hand and asks Pydantic to validate against the right discriminator.
    """
    if execution_mode == FlowExecutionMode.NO_AGENT_SCRIPT:
        try:
            ScriptModeConfig.model_validate(trigger_config or {})
        except Exception as e:
            raise ValidationFailed(str(e), code="flow.script_config_invalid") from e
    elif execution_mode == FlowExecutionMode.NO_AGENT_HTTP:
        try:
            HttpModeConfig.model_validate(trigger_config or {})
        except Exception as e:
            raise ValidationFailed(str(e), code="flow.http_config_invalid") from e


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
    merged_mode = patch.get("execution_mode", flow.execution_mode)
    merged_config = patch.get("trigger_config", flow.trigger_config) or {}
    _validate_merged_no_agent_config(execution_mode=merged_mode, trigger_config=merged_config)
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


@router.post(
    "/{flow_id}/run",
    response_model=FlowRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("flow_trigger", 30, 60))],
)
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


@router.post(
    "/{flow_id}/test-script",
    response_model=FlowTestResult,
    dependencies=[Depends(rate_limit("flow_test_script", 5, 60))],
)
async def test_script(
    flow_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    override: dict | None = Body(default=None),
) -> FlowTestResult:
    """Workspace-admin dry-run for ``no_agent_script`` flows.

    Does NOT persist a FlowRun; does NOT audit the body of stdout/stderr;
    DOES audit the fact that a test was triggered. The override body lets
    an admin tweak ``trigger_config`` in the editor and validate before
    saving.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    payload = await svc.dry_run_script(flow, override_config=override)
    await audit_svc.record(
        db,
        action="flow.test_script",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"dry-run script test for flow {flow.name!r}",
        metadata={
            "outcome": payload["outcome"].value,
            "duration_ms": payload["duration_ms"],
            "exit_code": payload.get("exit_code"),
        },
        request=request,
    )
    await db.commit()
    return FlowTestResult.model_validate(
        {
            "outcome": payload["outcome"],
            "duration_ms": payload["duration_ms"],
            "exit_code": payload.get("exit_code"),
            "output_excerpt": payload.get("output_excerpt"),
            "error": payload.get("error"),
        }
    )


@router.post(
    "/{flow_id}/test-http",
    response_model=FlowTestResult,
    dependencies=[Depends(rate_limit("flow_test_http", 10, 60))],
)
async def test_http(
    flow_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    override: dict | None = Body(default=None),
) -> FlowTestResult:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    flow = await svc.get_or_404(db, flow_id, workspace_id=ws_id)
    payload = await svc.dry_run_http(db, flow, override_config=override)
    await audit_svc.record(
        db,
        action="flow.test_http",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=f"dry-run http test for flow {flow.name!r}",
        metadata={
            "outcome": payload["outcome"].value,
            "duration_ms": payload["duration_ms"],
            "status": payload.get("response_status"),
        },
        request=request,
    )
    await db.commit()
    return FlowTestResult.model_validate(
        {
            "outcome": payload["outcome"],
            "duration_ms": payload["duration_ms"],
            "response_status": payload.get("response_status"),
            "error": payload.get("error"),
        }
    )
