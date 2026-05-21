"""Runtime console endpoints (M4.1).

Three routes back the workspace ``/settings/system/runtime`` page:

* ``GET /admin/runtime/inflight-runs`` — list every live or recently
  terminal :class:`InflightRun` for the active workspace, enriched
  with session label / agent name / owner email so the admin can
  triage without round-tripping to other endpoints.
* ``POST /admin/runtime/inflight-runs/{run_id}/force-recycle`` — issue
  a best-effort kernel cancel + flip the spine row to ``CANCELLED``
  with ``error_kind=admin_force_recycle``. Audits +
  ``inflight_run.force_recycled`` notification land on the actor.
* ``GET /admin/runtime/stats`` — counter card payload for the
  dashboard top strip (running / paused / lost / zombie / killed +
  total active).

Authorization is workspace admin or platform admin — every route uses
the same gate so the dashboard sees a consistent view across all
three calls. Rate limits keep the 5-second polling client well below
the per-bucket Redis cost while still allowing a click-happy admin to
hit force-recycle a few times in a row.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity, PlatformRole
from app.db.models.inflight_run import InflightRunState
from app.repositories.identity import IdentityRepository
from app.services import inflight_run as inflight_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/admin/runtime", tags=["admin", "runtime"])


# ─── Auth gate ──────────────────────────────────────────────
async def _require_workspace_admin(
    *,
    workspace_id: uuid.UUID | None,
    db: DBSession,
    identity_id: uuid.UUID,
) -> tuple[Identity, uuid.UUID]:
    """Allow platform admins outright; otherwise require workspace admin.

    Returns ``(identity, resolved_workspace_id)``. Platform admins must
    still pass ``X-Workspace-Id`` so the listing is scoped to a single
    tenant — there's no cross-workspace view in M4.1 (the whitepaper
    keeps each workspace's runtime view independent for blast-radius
    reasons).
    """
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_required",
        )
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise Unauthorized("identity_missing", code="auth.no_identity")
    if identity.platform_role != PlatformRole.PLATFORM_ADMIN:
        await ws_svc.ensure_admin(
            db, workspace_id=workspace_id, identity_id=identity_id
        )
    return identity, workspace_id


# ─── DTOs ───────────────────────────────────────────────────
class InflightRunRow(BaseModel):
    """Wire shape for one runtime-console row."""

    inflight_run_id: uuid.UUID
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    session_label: str | None = None
    agent_id: uuid.UUID | None = None
    agent_name: str | None = None
    identity_id: uuid.UUID | None = None
    identity_email: str | None = None
    state: InflightRunState
    state_bucket: str
    backend_kind: str
    started_at: str
    last_seen_at: str
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    last_event_seq: int = 0
    token_estimate: int | None = None
    error_kind: str | None = None


class InflightRunList(BaseModel):
    rows: list[InflightRunRow] = Field(default_factory=list)
    total: int = 0


class RuntimeStatsOut(BaseModel):
    running: int
    paused: int
    lost: int
    zombie: int
    killed: int
    total_active: int


class ForceRecycleResult(BaseModel):
    run_id: str
    inflight_run_id: str
    state: str
    previous_state: str
    killed_at: str
    cancel_dispatched: bool
    cancel_error: str | None = None


# ─── Helpers ────────────────────────────────────────────────
def _parse_state_filter(raw: str | None) -> list[str]:
    """Split ``?state=running,paused`` into a normalized bucket list."""
    if not raw:
        return []
    return [piece for piece in raw.split(",") if piece.strip()]


def _row_to_payload(row: inflight_svc.InflightRunWithMeta) -> InflightRunRow:
    return InflightRunRow(
        inflight_run_id=row.inflight_run_id,
        run_id=row.run_id,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        session_label=row.session_label,
        agent_id=row.agent_id,
        agent_name=row.agent_name,
        identity_id=row.identity_id,
        identity_email=row.identity_email,
        state=row.state,
        state_bucket=row.state_bucket,
        backend_kind=row.backend_kind,
        started_at=row.started_at.isoformat(),
        last_seen_at=row.last_seen_at.isoformat(),
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        elapsed_seconds=float(row.elapsed_seconds),
        last_event_seq=int(row.last_event_seq),
        token_estimate=row.token_estimate,
        error_kind=row.error_kind,
    )


# ─── Routes ─────────────────────────────────────────────────
@router.get(
    "/inflight-runs",
    response_model=InflightRunList,
    dependencies=[
        Depends(rate_limit("runtime_console_read", limit=60, period_seconds=60)),
    ],
)
async def list_inflight_runs(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    state: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated bucket filter "
                "(running / paused / lost / zombie / killed)."
            ),
        ),
    ] = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> InflightRunList:
    """List the workspace's live + recently-terminal inflight runs."""
    _identity, ws_id = await _require_workspace_admin(
        workspace_id=workspace_id, db=db, identity_id=identity_id
    )

    rows = await inflight_svc.list_active_for_console(
        db,
        workspace_id=ws_id,
        limit=limit,
        states=_parse_state_filter(state) or None,
    )
    return InflightRunList(
        rows=[_row_to_payload(row) for row in rows],
        total=len(rows),
    )


@router.get(
    "/stats",
    response_model=RuntimeStatsOut,
    dependencies=[
        Depends(rate_limit("runtime_console_stats", limit=30, period_seconds=60)),
    ],
)
async def runtime_stats(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> RuntimeStatsOut:
    """Counter card payload for the dashboard top strip."""
    _identity, ws_id = await _require_workspace_admin(
        workspace_id=workspace_id, db=db, identity_id=identity_id
    )

    stats = await inflight_svc.runtime_console_stats(db, workspace_id=ws_id)
    return RuntimeStatsOut(
        running=stats.running,
        paused=stats.paused,
        lost=stats.lost,
        zombie=stats.zombie,
        killed=stats.killed,
        total_active=stats.total_active,
    )


@router.post(
    "/inflight-runs/{run_id}/force-recycle",
    response_model=ForceRecycleResult,
    dependencies=[
        Depends(
            rate_limit("runtime_console_recycle", limit=5, period_seconds=60)
        ),
    ],
)
async def force_recycle_inflight_run(
    run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ForceRecycleResult:
    """Cancel a live run + transition the spine row to CANCELLED.

    Returns a 404 when the run id doesn't exist or belongs to a
    different workspace; returns a 409 when the row already settled
    (admins shouldn't double-click their way into duplicate audits).
    """
    actor, ws_id = await _require_workspace_admin(
        workspace_id=workspace_id, db=db, identity_id=identity_id
    )

    try:
        result = await inflight_svc.force_recycle_run(
            db,
            workspace_id=ws_id,
            run_id=run_id,
            actor_identity_id=actor.id,
            request=request,
        )
    except inflight_svc.RunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="inflight_run_not_found"
        ) from exc
    except inflight_svc.RunTerminalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"inflight_run_already_terminal:{exc.state.value}",
        ) from exc

    await db.commit()
    return ForceRecycleResult(**result)
