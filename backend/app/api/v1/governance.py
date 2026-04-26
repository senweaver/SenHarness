"""Governance APIs: policies, budgets, usage events, and tool call logs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.api.v1.admin import require_platform_admin
from app.core.errors import NotFound, Unauthorized
from app.db.models.governance import GovernanceScope
from app.repositories.governance import (
    BudgetRepository,
    PolicyRepository,
    ToolCallLogRepository,
    UsageEventRepository,
)
from app.schemas.governance import (
    BudgetCreate,
    BudgetRead,
    BudgetUpdate,
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    ToolCallLogCreate,
    ToolCallLogRead,
    UsageEventCreate,
    UsageEventRead,
)
from app.services import governance as gov_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/governance", tags=["governance"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _is_global(scope: GovernanceScope | str) -> bool:
    return GovernanceScope(str(scope)) == GovernanceScope.GLOBAL


async def _require_scope_access(
    *,
    db: DBSession,
    identity_id: uuid.UUID,
    active_workspace_id: uuid.UUID | None,
    scope: GovernanceScope | str,
) -> None:
    if _is_global(scope):
        await require_platform_admin(db=db, identity_id=identity_id)
        return
    ws_id = _require_workspace(active_workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)


async def _get_policy_or_404(
    db: DBSession,
    *,
    policy_id: uuid.UUID,
    active_workspace_id: uuid.UUID,
) -> object:
    row = await PolicyRepository(db).get(policy_id)
    if row is None:
        raise NotFound("policy_not_found", code="governance.policy_not_found")
    if row.scope != GovernanceScope.GLOBAL and row.workspace_id != active_workspace_id:
        raise NotFound("policy_not_found", code="governance.policy_not_found")
    return row


async def _get_budget_or_404(
    db: DBSession,
    *,
    budget_id: uuid.UUID,
    active_workspace_id: uuid.UUID,
) -> object:
    row = await BudgetRepository(db).get(budget_id)
    if row is None:
        raise NotFound("budget_not_found", code="governance.budget_not_found")
    if row.scope != GovernanceScope.GLOBAL and row.workspace_id != active_workspace_id:
        raise NotFound("budget_not_found", code="governance.budget_not_found")
    return row


@router.get("/policies", response_model=list[PolicyRead])
async def list_policies(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = 0,
    limit: int = 200,
) -> list[PolicyRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await PolicyRepository(db).list_visible_for_workspace(
        workspace_id=ws_id, offset=offset, limit=max(1, min(limit, 500))
    )
    return [PolicyRead.model_validate(r) for r in rows]


@router.post("/policies", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> PolicyRead:
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=body.scope,
    )
    normalized_workspace_id, normalized_agent_id = gov_svc.ensure_scope_targets(
        scope=body.scope,
        workspace_id=body.workspace_id,
        agent_id=body.agent_id,
        active_workspace_id=workspace_id,
        allow_global=True,
    )
    row = await PolicyRepository(db).create(
        scope=body.scope,
        workspace_id=normalized_workspace_id,
        agent_id=normalized_agent_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        priority=body.priority,
        rules_json=body.rules_json,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await db.commit()
    return PolicyRead.model_validate(row)


@router.patch("/policies/{policy_id}", response_model=PolicyRead)
async def update_policy(
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> PolicyRead:
    ws_id = _require_workspace(workspace_id)
    row = await _get_policy_or_404(db, policy_id=policy_id, active_workspace_id=ws_id)

    target_scope = body.scope or row.scope
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=target_scope,
    )
    patch = body.model_dump(exclude_none=True)
    normalized_workspace_id, normalized_agent_id = gov_svc.ensure_scope_targets(
        scope=target_scope,
        workspace_id=patch.get("workspace_id", row.workspace_id),
        agent_id=patch.get("agent_id", row.agent_id),
        active_workspace_id=workspace_id,
        allow_global=True,
    )
    patch["scope"] = target_scope
    patch["workspace_id"] = normalized_workspace_id
    patch["agent_id"] = normalized_agent_id

    row = await PolicyRepository(db).update(row, **patch)
    await db.commit()
    return PolicyRead.model_validate(row)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    row = await _get_policy_or_404(db, policy_id=policy_id, active_workspace_id=ws_id)
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=row.scope,
    )
    await PolicyRepository(db).soft_delete(row)
    await db.commit()


@router.get("/budgets", response_model=list[BudgetRead])
async def list_budgets(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = 0,
    limit: int = 200,
) -> list[BudgetRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await BudgetRepository(db).list_visible_for_workspace(
        workspace_id=ws_id, offset=offset, limit=max(1, min(limit, 500))
    )
    return [BudgetRead.model_validate(r) for r in rows]


@router.post("/budgets", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(
    body: BudgetCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BudgetRead:
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=body.scope,
    )
    normalized_workspace_id, normalized_agent_id = gov_svc.ensure_scope_targets(
        scope=body.scope,
        workspace_id=body.workspace_id,
        agent_id=body.agent_id,
        active_workspace_id=workspace_id,
        allow_global=True,
    )
    row = await BudgetRepository(db).create(
        scope=body.scope,
        workspace_id=normalized_workspace_id,
        agent_id=normalized_agent_id,
        name=body.name,
        currency=body.currency.upper(),
        period=body.period,
        limit_amount=body.limit_amount,
        alert_threshold_pct=body.alert_threshold_pct,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await db.commit()
    return BudgetRead.model_validate(row)


@router.patch("/budgets/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: uuid.UUID,
    body: BudgetUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BudgetRead:
    ws_id = _require_workspace(workspace_id)
    row = await _get_budget_or_404(db, budget_id=budget_id, active_workspace_id=ws_id)

    target_scope = body.scope or row.scope
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=target_scope,
    )
    patch = body.model_dump(exclude_none=True)
    normalized_workspace_id, normalized_agent_id = gov_svc.ensure_scope_targets(
        scope=target_scope,
        workspace_id=patch.get("workspace_id", row.workspace_id),
        agent_id=patch.get("agent_id", row.agent_id),
        active_workspace_id=workspace_id,
        allow_global=True,
    )
    patch["scope"] = target_scope
    patch["workspace_id"] = normalized_workspace_id
    patch["agent_id"] = normalized_agent_id
    if "currency" in patch:
        patch["currency"] = str(patch["currency"]).upper()

    row = await BudgetRepository(db).update(row, **patch)
    await db.commit()
    return BudgetRead.model_validate(row)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    row = await _get_budget_or_404(db, budget_id=budget_id, active_workspace_id=ws_id)
    await _require_scope_access(
        db=db,
        identity_id=identity_id,
        active_workspace_id=workspace_id,
        scope=row.scope,
    )
    await BudgetRepository(db).soft_delete(row)
    await db.commit()


@router.get("/usage-events", response_model=list[UsageEventRead])
async def list_usage_events(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = 0,
    limit: int = 200,
) -> list[UsageEventRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await UsageEventRepository(db).list_for_workspace(
        workspace_id=ws_id, offset=offset, limit=max(1, min(limit, 500))
    )
    return [UsageEventRead.model_validate(r) for r in rows]


@router.post(
    "/usage-events",
    response_model=UsageEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create usage event (internal ingestion endpoint)",
)
async def create_usage_event(
    body: UsageEventCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> UsageEventRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await UsageEventRepository(db).create(
        workspace_id=body.workspace_id or ws_id,
        agent_id=body.agent_id,
        session_id=body.session_id,
        policy_id=body.policy_id,
        budget_id=body.budget_id,
        event_type=body.event_type,
        provider=body.provider,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost_usd=body.cost_usd,
        tool_name=body.tool_name,
        metadata_json=body.metadata_json,
    )
    await db.commit()
    return UsageEventRead.model_validate(row)


@router.get("/tool-call-logs", response_model=list[ToolCallLogRead])
async def list_tool_call_logs(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = 0,
    limit: int = 200,
) -> list[ToolCallLogRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await ToolCallLogRepository(db).list_for_workspace(
        workspace_id=ws_id, offset=offset, limit=max(1, min(limit, 500))
    )
    return [ToolCallLogRead.model_validate(r) for r in rows]


@router.post(
    "/tool-call-logs",
    response_model=ToolCallLogRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create tool call log (internal ingestion endpoint)",
)
async def create_tool_call_log(
    body: ToolCallLogCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ToolCallLogRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await ToolCallLogRepository(db).create(
        workspace_id=body.workspace_id or ws_id,
        agent_id=body.agent_id,
        session_id=body.session_id,
        policy_id=body.policy_id,
        tool_name=body.tool_name,
        status=body.status,
        duration_ms=body.duration_ms,
        input_json=body.input_json,
        output_json=body.output_json,
        error_text=body.error_text,
        cost_usd=body.cost_usd,
        metadata_json=body.metadata_json,
    )
    await db.commit()
    return ToolCallLogRead.model_validate(row)
