"""Read-only REST surface for captured session artifacts (M0.2).

Capture is *server-internal* — there's no POST endpoint here. Artifacts
are emitted from the WS turn handler and the channel/flow runner via
:func:`app.services.session_artifact.capture_from_run_outcome`. The
routes below let workspace members and admins read them back for the
debug drawer (M0.2 frontend), the M0.3 Curator backlog, and the M2+
Evolver candidate pipeline.

RBAC:

* Session-scoped routes require active workspace membership; the
  per-session leak check goes through ``ensure_member_access`` +
  ``get_session_or_404``.
* The workspace recent feed requires workspace admin/owner because it
  enumerates *every* session's artifacts in the tenant — sensitive
  enough to warrant tighter gating than per-session reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.schemas.judge_verdict import JudgeSessionSummary, JudgeVerdictRead
from app.schemas.session_artifact import SessionArtifactRead
from app.services import audit as audit_svc
from app.services import judge as judge_svc
from app.services import session as session_svc
from app.services import session_artifact as artifact_svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Per-session list ────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/artifacts",
    response_model=list[SessionArtifactRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("session_artifact_read", limit=120, period_seconds=60))],
    tags=["sessions"],
)
async def list_session_artifacts(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionArtifactRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await session_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await artifact_svc.list_artifacts_for_session(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return [SessionArtifactRead.model_validate(r) for r in rows]


# ─── Single artifact ─────────────────────────────────────────
@router.get(
    "/artifacts/{artifact_id}",
    response_model=SessionArtifactRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("session_artifact_read", limit=120, period_seconds=60))],
    tags=["sessions"],
)
async def get_session_artifact(
    artifact_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionArtifactRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await artifact_svc.get_artifact_by_id(db, workspace_id=ws_id, artifact_id=artifact_id)
    return SessionArtifactRead.model_validate(row)


# ─── Workspace-wide recent (admin) ───────────────────────────
@router.get(
    "/workspaces/{workspace_id}/artifacts/recent",
    response_model=list[SessionArtifactRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("artifact_admin_read", limit=60, period_seconds=60))],
    tags=["workspaces"],
)
async def list_recent_workspace_artifacts(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    active_workspace_id: CurrentWorkspaceId,
    since_hours: int = Query(24, ge=1, le=24 * 30),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[SessionArtifactRead]:
    """Recent artifacts in a workspace, newest first.

    The path-level ``workspace_id`` must match the caller's active
    workspace — cross-tenant reads route through the platform-admin
    surface, not this endpoint.
    """
    active = _require_workspace(active_workspace_id)
    if active != workspace_id:
        raise NotFound("workspace not found", code="workspace.not_found")
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)
    since = datetime.utcnow() - timedelta(hours=int(since_hours))
    rows = await artifact_svc.list_recent_for_workspace(
        db,
        workspace_id=workspace_id,
        since=since,
        limit=limit,
        offset=offset,
    )
    return [SessionArtifactRead.model_validate(r) for r in rows]


# ─── M0.3 — Judge verdict reads + rejudge ────────────────────
@router.get(
    "/artifacts/{artifact_id}/verdict",
    response_model=JudgeVerdictRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("artifact_verdict_read", limit=120, period_seconds=60))],
    tags=["sessions"],
)
async def get_artifact_verdict(
    artifact_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> JudgeVerdictRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    # Re-use the existing artifact reader so cross-workspace probes
    # produce the same NotFound shape rather than leaking via verdict
    # absence.
    await artifact_svc.get_artifact_by_id(db, workspace_id=ws_id, artifact_id=artifact_id)
    verdict = await judge_svc.get_verdict(db, workspace_id=ws_id, artifact_id=artifact_id)
    if verdict is None:
        raise NotFound("verdict not found", code="judge.verdict_not_found")
    return JudgeVerdictRead.model_validate(verdict)


@router.post(
    "/artifacts/{artifact_id}/rejudge",
    response_model=SessionArtifactRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("artifact_rejudge", limit=5, period_seconds=60))],
    tags=["sessions"],
)
async def rejudge_artifact(
    artifact_id: uuid.UUID,
    db: DBSession,
    request: Request,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionArtifactRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    artifact = await judge_svc.request_rejudge(
        db,
        workspace_id=ws_id,
        artifact_id=artifact_id,
        actor_identity_id=identity_id,
    )
    await audit_svc.record(
        db,
        action="judge.rejudge_requested",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="session_artifact",
        resource_id=artifact_id,
        summary="manual rejudge requested",
        metadata={"artifact_id": str(artifact_id)},
        request=request,
    )
    await db.commit()

    try:
        from app.worker.queue import enqueue

        await enqueue("judge_session_artifact", str(artifact_id), _defer_by=2)
    except Exception:  # pragma: no cover
        # Audit the queue miss so admins know the manual rejudge needs
        # the periodic sweep to pick it up.
        await audit_svc.record(
            db,
            action="judge.enqueue_failed",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="session_artifact",
            resource_id=artifact_id,
            summary="rejudge enqueue failed",
            metadata={"artifact_id": str(artifact_id), "trigger": "rejudge"},
            request=request,
        )
        await db.commit()

    return SessionArtifactRead.model_validate(artifact)


@router.get(
    "/sessions/{session_id}/artifacts/judge-summary",
    response_model=JudgeSessionSummary,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit("artifact_verdict_read", limit=120, period_seconds=60))],
    tags=["sessions"],
)
async def get_session_judge_summary(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> JudgeSessionSummary:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await session_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    counts = await judge_svc.session_summary(db, workspace_id=ws_id, session_id=session_id)
    return JudgeSessionSummary(session_id=session_id, **counts)
