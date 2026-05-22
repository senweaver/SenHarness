"""Admin invoke endpoint for the platform-builtin evolver subagent (M2.2).

Single route — ``POST /admin/workspaces/{workspace_id}/evolver/invoke``
— that lets a platform admin or a workspace admin trigger a one-shot
evolver run on demand. The cron / workflow path (M2.3) does not flow
through this module; it calls
:func:`app.agents.builtin.evolver_agent.invoke_evolver_subagent`
directly. Splitting the surfaces keeps the admin endpoint thin —
auth, rate limit, error mapping — while the actual run logic stays
in the builtin agent module.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agents.builtin.evolver_agent import (
    EvolverAuxModelMissingError,
    EvolverBreakerOpenError,
    EvolverDisabledError,
    EvolverInvokeResult,
    invoke_evolver_subagent,
)
from app.api.deps import CurrentIdentityId, DBSession
from app.core.errors import AppError
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity, PlatformRole
from app.repositories.identity import IdentityRepository
from app.services import workspace as ws_svc

router = APIRouter(tags=["admin", "evolver"])


# ─── DTOs ────────────────────────────────────────────────────
class EvolverInvokeIn(BaseModel):
    triggering_run_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class EvolverInvokeOut(BaseModel):
    run_id: uuid.UUID
    proposals_created: int
    skipped: bool
    duration_ms: int
    final_message: str | None
    error: str | None
    aux_model: str | None
    timed_out: bool


# ─── Error mappings ──────────────────────────────────────────
class _EvolverDisabledHttp(AppError):
    code = "evolver.disabled"
    default_status = 409


class _EvolverBreakerHttp(AppError):
    code = "evolver.breaker_tripped"
    default_status = 503


class _EvolverAuxMissingHttp(AppError):
    code = "evolver.aux_model_missing"
    default_status = 412


# ─── Auth gate ───────────────────────────────────────────────
async def _require_evolver_admin(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> Identity:
    """Allow platform admins outright; otherwise require workspace admin.

    Either identity record is returned so the caller can stamp audit
    rows with the actor id. Workspace admins are authorised via
    :func:`app.services.workspace.ensure_admin` (owner + admin role).
    """
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        from app.core.errors import Unauthorized

        raise Unauthorized("identity_missing", code="auth.no_identity")
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    return identity


# ─── Route ───────────────────────────────────────────────────
@router.post(
    "/admin/workspaces/{workspace_id}/evolver/invoke",
    response_model=EvolverInvokeOut,
    dependencies=[
        Depends(rate_limit("evolver_admin_invoke", limit=3, period_seconds=300)),
    ],
)
async def admin_invoke_evolver(
    workspace_id: uuid.UUID,
    body: EvolverInvokeIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> EvolverInvokeOut:
    actor = await _require_evolver_admin(workspace_id=workspace_id, db=db, identity_id=identity_id)
    _ = request

    try:
        result: EvolverInvokeResult = await invoke_evolver_subagent(
            workspace_id=workspace_id,
            triggering_run_ids=list(body.triggering_run_ids) or None,
            invocation_kind="manual",
            actor_identity_id=actor.id,
        )
    except EvolverDisabledError as exc:
        raise _EvolverDisabledHttp(str(exc)) from exc
    except EvolverBreakerOpenError as exc:
        raise _EvolverBreakerHttp(str(exc)) from exc
    except EvolverAuxModelMissingError as exc:
        raise _EvolverAuxMissingHttp(str(exc)) from exc

    # ``invoke_evolver_subagent`` opens its own short-lived sessions
    # for the audit rows; nothing on this request session needs a
    # commit. Keep the commit anyway so any future addition to the
    # session (e.g. fetching the actor identity above lazily loaded
    # related data) flushes deterministically.
    await db.commit()

    payload: dict[str, Any] = {
        "run_id": result.run_id,
        "proposals_created": int(result.proposals_created),
        "skipped": bool(result.skipped),
        "duration_ms": int(result.duration_ms),
        "final_message": result.final_message,
        "error": result.error,
        "aux_model": result.aux_model,
        "timed_out": bool(result.timed_out),
    }
    return EvolverInvokeOut(**payload)
