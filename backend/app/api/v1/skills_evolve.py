"""Manual evolver workflow trigger + last-run readout (M2.3).

Two endpoints:

* ``POST /skills/evolve/trigger`` — workspace admin (or platform
  admin) fires :func:`evolve_workspace_skills` synchronously and
  receives the structured :class:`WorkflowExecutionResult`. The body
  may set ``bypass_min_artifacts=True`` so an admin can drive an
  ad-hoc run before the daily ARQ cron has accumulated five failing
  artifacts; the workspace's ``evolver.enabled`` flag is **not**
  bypassed.
* ``GET /skills/evolve/last-run/{workspace_id}`` — return the most
  recent ``evolver.workflow_completed`` audit row for the workspace
  so the admin UI can render "last run on YYYY-MM-DD: K proposals
  from N artifacts" without spelunking the audit feed manually.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import CurrentIdentityId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.audit import AuditEvent
from app.db.models.identity import Identity, PlatformRole
from app.repositories.identity import IdentityRepository
from app.services import audit as audit_svc
from app.services import workspace as ws_svc
from app.services.evolver_workflow import (
    AUDIT_MANUALLY_TRIGGERED,
    AUDIT_WORKFLOW_COMPLETED,
    WorkflowExecutionResult,
    evolve_workspace_skills,
)

router = APIRouter(tags=["skills", "evolver"])


# ─── DTOs ────────────────────────────────────────────────────
class TriggerEvolutionRequest(BaseModel):
    """Body for ``POST /skills/evolve/trigger``."""

    workspace_id: uuid.UUID
    bypass_min_artifacts: bool = Field(
        default=False,
        description=(
            "When True, the min_artifacts_per_evolution gate is "
            "bypassed. The workspace's enabled flag is still enforced."
        ),
    )


class WorkflowExecutionResultOut(BaseModel):
    workspace_id: uuid.UUID
    engine: Literal["workflow", "agent"]
    invocation_kind: Literal["scheduled", "manual"]
    artifacts_drained: int
    artifacts_summarized: int
    proposals_created: int
    skipped: bool
    skip_reason: str | None
    duration_ms: int
    error: str | None
    aux_model: str | None

    @classmethod
    def from_dataclass(cls, result: WorkflowExecutionResult) -> WorkflowExecutionResultOut:
        return cls(
            workspace_id=result.workspace_id,
            engine=result.engine,
            invocation_kind=result.invocation_kind,
            artifacts_drained=int(result.artifacts_drained),
            artifacts_summarized=int(result.artifacts_summarized),
            proposals_created=int(result.proposals_created),
            skipped=bool(result.skipped),
            skip_reason=result.skip_reason,
            duration_ms=int(result.duration_ms),
            error=result.error,
            aux_model=result.aux_model,
        )


class EvolverLastRunOut(BaseModel):
    workspace_id: uuid.UUID
    last_run_at: datetime | None
    engine: Literal["workflow", "agent"] | None
    invocation_kind: Literal["scheduled", "manual"] | None
    artifacts_drained: int | None
    proposals_created: int | None
    duration_ms: int | None
    aux_model: str | None
    summary: str | None


# ─── Auth gate (mirrors admin_evolver._require_evolver_admin) ─
async def _require_workspace_admin(
    *,
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: uuid.UUID,
) -> Identity:
    """Allow platform admins outright; otherwise require workspace admin."""
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise Unauthorized("identity_missing", code="auth.no_identity")
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    return identity


async def _require_workspace_member(
    *,
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: uuid.UUID,
) -> Identity:
    """Read-side gate — every workspace member can read the last run."""
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise Unauthorized("identity_missing", code="auth.no_identity")
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return identity
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    return identity


# ─── Routes ──────────────────────────────────────────────────
@router.post(
    "/skills/evolve/trigger",
    response_model=WorkflowExecutionResultOut,
    dependencies=[
        Depends(rate_limit("skills_evolve_trigger", limit=2, period_seconds=300)),
    ],
)
async def trigger_evolution(
    body: TriggerEvolutionRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> WorkflowExecutionResultOut:
    """Workspace admin only. Runs ``evolve_workspace_skills`` synchronously.

    Returns the same structured result the daily ARQ sweep emits. The
    ``evolver.workflow_completed`` (or ``_skipped`` / ``_failed``)
    audit is written by the workflow service itself; the API layer
    additionally writes ``evolver.manually_triggered`` so the UI can
    distinguish admin-driven runs from cron runs at a glance.
    """
    _ = request
    actor = await _require_workspace_admin(
        workspace_id=body.workspace_id, db=db, identity_id=identity_id
    )

    result = await evolve_workspace_skills(
        db,
        workspace_id=body.workspace_id,
        invocation_kind="manual",
        actor_identity_id=actor.id,
        bypass_min_artifacts=body.bypass_min_artifacts,
    )

    await audit_svc.record(
        db,
        action=AUDIT_MANUALLY_TRIGGERED,
        actor_identity_id=actor.id,
        workspace_id=body.workspace_id,
        resource_type="workspace",
        resource_id=body.workspace_id,
        summary=(
            f"evolver workflow manually triggered "
            f"({result.engine}, bypass={body.bypass_min_artifacts})"
        ),
        metadata={
            "engine": result.engine,
            "bypass_min_artifacts": bool(body.bypass_min_artifacts),
            "proposals_created": int(result.proposals_created),
            "skipped": bool(result.skipped),
            "skip_reason": result.skip_reason,
            "duration_ms": int(result.duration_ms),
            "aux_model": result.aux_model,
            "error": result.error,
        },
    )
    await db.commit()

    return WorkflowExecutionResultOut.from_dataclass(result)


@router.get(
    "/skills/evolve/last-run/{workspace_id}",
    response_model=EvolverLastRunOut,
    dependencies=[
        Depends(rate_limit("skills_evolve_history_read", limit=30, period_seconds=60)),
    ],
)
async def get_last_evolution(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> EvolverLastRunOut:
    """Return the most recent ``evolver.workflow_completed`` audit row.

    When no completed run has landed yet (first daily cron hasn't
    fired or every run has been a skip), the response carries
    ``last_run_at=None`` and the rest of the fields default to
    ``None`` so the admin UI can render an explicit "never run" state.
    """
    await _require_workspace_member(workspace_id=workspace_id, db=db, identity_id=identity_id)

    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == AUDIT_WORKFLOW_COMPLETED,
        )
        .order_by(desc(AuditEvent.created_at))
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return EvolverLastRunOut(
            workspace_id=workspace_id,
            last_run_at=None,
            engine=None,
            invocation_kind=None,
            artifacts_drained=None,
            proposals_created=None,
            duration_ms=None,
            aux_model=None,
            summary=None,
        )

    meta: dict[str, Any] = row.metadata_json or {}
    engine = meta.get("engine") if meta.get("engine") in {"workflow", "agent"} else None
    invocation = meta.get("invocation_kind")
    if invocation not in {"scheduled", "manual"}:
        invocation = None

    return EvolverLastRunOut(
        workspace_id=workspace_id,
        last_run_at=row.created_at,
        engine=engine,
        invocation_kind=invocation,
        artifacts_drained=_coerce_int(meta.get("artifacts_drained")),
        proposals_created=_coerce_int(meta.get("proposals_created")),
        duration_ms=_coerce_int(meta.get("duration_ms")),
        aux_model=meta.get("aux_model") if isinstance(meta.get("aux_model"), str) else None,
        summary=row.summary,
    )


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
