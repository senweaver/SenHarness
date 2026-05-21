"""Read-only aux-config readout for workspace admins (M0.3).

Surfaces the merged ``aux`` settings, current breaker status, and the
sliding-window rate budget usage for the run-quality judge so the
``settings/workspace/providers`` page can render a non-editable
diagnostics card. Editing of aux model picks lands in M0.13's
schema-driven settings panel.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.agents.auxiliary_client import get_workspace_aux_settings
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.jobs._breaker import current_rate_usage, is_breaker_open
from app.services import workspace as ws_svc

router = APIRouter()


_JUDGE_BUCKET = "judge_run"


class AuxConfigBreakerOut(BaseModel):
    open: bool
    fail_strikes: int = Field(ge=0)
    fail_window_seconds: int = Field(ge=1)
    recover_seconds: int = Field(ge=1)


class AuxConfigRateOut(BaseModel):
    limit: int = Field(ge=0)
    used: int = Field(ge=0)
    period_seconds: int = Field(ge=1)


class AuxConfigReadOut(BaseModel):
    workspace_id: uuid.UUID
    aux_model_default: str | None
    aux_model_judge: str | None
    aux_model_goal_alignment: str | None
    judge_breaker: AuxConfigBreakerOut
    judge_rate: AuxConfigRateOut


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized(
            "no_active_workspace", code="auth.no_active_workspace"
        )
    return workspace_id


@router.get(
    "/workspaces/{workspace_id}/aux-config",
    response_model=AuxConfigReadOut,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("aux_config_read", limit=60, period_seconds=60))
    ],
    tags=["workspaces"],
)
async def get_aux_config(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    active_workspace_id: CurrentWorkspaceId,
) -> AuxConfigReadOut:
    """Return the aux-config readout for the active workspace.

    Workspace admins (or platform admins) only — the aux model name
    can leak provider choice to a curious guest, so we keep this
    behind the same admin gate as the recent-artifacts feed.
    """
    active = _require_workspace(active_workspace_id)
    if active != workspace_id:
        raise NotFound("workspace not found", code="workspace.not_found")
    await ws_svc.ensure_admin(
        db, workspace_id=workspace_id, identity_id=identity_id
    )

    settings = await get_workspace_aux_settings(db, workspace_id=workspace_id)
    fail_strikes = int(settings.get("judge_fail_strikes") or 5)
    fail_window = int(settings.get("judge_fail_window_seconds") or 300)
    recover_seconds = int(
        settings.get("judge_breaker_recover_seconds") or 3600
    )
    rate_limit_per_min = int(settings.get("judge_rate_per_minute") or 60)

    open_state = await is_breaker_open(
        bucket=_JUDGE_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=fail_strikes,
    )
    used = await current_rate_usage(
        bucket=_JUDGE_BUCKET,
        workspace_id=str(workspace_id),
        period_seconds=60,
    )

    return AuxConfigReadOut(
        workspace_id=workspace_id,
        aux_model_default=settings.get("aux_model_default"),
        aux_model_judge=settings.get("aux_model_judge"),
        aux_model_goal_alignment=settings.get("aux_model_goal_alignment"),
        judge_breaker=AuxConfigBreakerOut(
            open=bool(open_state),
            fail_strikes=fail_strikes,
            fail_window_seconds=fail_window,
            recover_seconds=recover_seconds,
        ),
        judge_rate=AuxConfigRateOut(
            limit=rate_limit_per_min,
            used=int(used),
            period_seconds=60,
        ),
    )
