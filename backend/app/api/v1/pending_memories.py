"""Pending memory queue REST surface (M0.7).

Read + cancel endpoints for the per-session drawer, an aggregate
stats endpoint for the workspace-admin dashboard, and a
platform-admin debug ``promote-now`` trigger that runs the same
sweep the cron normally drives.

RBAC is layered:

* ``GET /sessions/{id}/pending-memories`` — workspace member access
  to the parent session;
* ``POST /sessions/{id}/pending-memories/{pid}/cancel`` — session
  owner or workspace admin (the workspace admin gate is the same
  one ``ws_svc.ensure_admin`` enforces elsewhere);
* ``GET /workspaces/{id}/pending-memories/stats`` — workspace admin;
* ``POST /admin/pending-memories/promote-now`` — platform admin.

Every write writes an audit row (``pending_memory.cancelled`` /
``memory.promotion_completed`` / ``admin.pending_memory.trigger``)
through :func:`app.services.audit.record`. Rate limits live next to
the route declaration so an operator pruning ``rate_limit`` from one
endpoint can't also strip the audit by accident.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.api.v1.admin import AdminGate
from app.core.errors import NotFound, PermissionDenied, Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity
from app.db.models.role import BuiltinRole
from app.repositories.session import SessionRepository
from app.repositories.workspace import MembershipRepository
from app.schemas.pending_memory import (
    PendingMemoryRead,
    PendingMemoryStats,
    PromoteSweepResult,
)
from app.services import audit as audit_svc
from app.services import pending_memory as pending_memory_svc
from app.services import session as session_svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Per-session list ────────────────────────────────────────
@router.get(
    "/sessions/{session_id}/pending-memories",
    response_model=list[PendingMemoryRead],
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("pending_memory_read", limit=60, period_seconds=60))
    ],
    tags=["sessions", "memory"],
)
async def list_session_pending_memories(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[PendingMemoryRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await session_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await pending_memory_svc.list_session_pending(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return [PendingMemoryRead.model_validate(r) for r in rows]


# ─── Cancel a pending row ────────────────────────────────────
@router.post(
    "/sessions/{session_id}/pending-memories/{pending_id}/cancel",
    response_model=PendingMemoryRead,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("pending_memory_cancel", limit=10, period_seconds=60))
    ],
    tags=["sessions", "memory"],
)
async def cancel_pending_memory(
    session_id: uuid.UUID,
    pending_id: uuid.UUID,
    db: DBSession,
    request: Request,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> PendingMemoryRead:
    ws_id = _require_workspace(workspace_id)
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    session_obj = await session_svc.get_session_or_404(
        db, session_id, workspace_id=ws_id
    )

    is_owner = (
        getattr(session_obj, "owner_identity_id", None) == identity_id
        or getattr(session_obj, "created_by", None) == identity_id
    )
    is_admin = membership.role in {
        BuiltinRole.OWNER.value,
        BuiltinRole.ADMIN.value,
    }
    if not (is_owner or is_admin):
        raise PermissionDenied(
            "pending_memory_cancel_denied",
            code="pending_memory.cancel_denied",
        )

    row = await pending_memory_svc.cancel_pending_memory(
        db,
        workspace_id=ws_id,
        pending_id=pending_id,
        actor_identity_id=identity_id,
    )
    if row.session_id != session_id:
        raise NotFound(
            "pending_memory_not_found",
            code="pending_memory.not_found",
        )
    await db.commit()
    return PendingMemoryRead.model_validate(row)


# ─── Workspace-wide stats (admin) ────────────────────────────
@router.get(
    "/workspaces/{workspace_id}/pending-memories/stats",
    response_model=PendingMemoryStats,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(
            rate_limit(
                "pending_memory_admin_read", limit=30, period_seconds=60
            )
        )
    ],
    tags=["workspaces", "memory"],
)
async def workspace_pending_memory_stats(
    workspace_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    active_workspace_id: CurrentWorkspaceId,
) -> PendingMemoryStats:
    """Pending / promoted / skipped / failed counts for the workspace.

    Cross-tenant reads are not allowed — the path-level workspace must
    match the caller's active workspace, then the caller has to be an
    admin of that workspace. M0.13 may grow a platform-admin variant.
    """
    active = _require_workspace(active_workspace_id)
    if active != workspace_id:
        raise NotFound("workspace_not_found", code="workspace.not_found")
    await ws_svc.ensure_admin(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    stats = await pending_memory_svc.workspace_stats(
        db, workspace_id=workspace_id
    )
    return PendingMemoryStats.model_validate(stats)


# ─── Platform-admin: force a sweep tick ─────────────────────
@router.post(
    "/admin/pending-memories/promote-now",
    response_model=PromoteSweepResult,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(
            rate_limit(
                "admin_pending_memory_trigger",
                limit=5,
                period_seconds=300,
            )
        )
    ],
    tags=["admin", "memory"],
)
async def trigger_pending_memory_sweep(
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> PromoteSweepResult:
    """Run :func:`promote_pending_memories_workspace_sweep` for every
    active workspace in-process, returning the aggregated counts.

    Debug-only: production ticks come from the ARQ cron. The endpoint
    blocks the request thread for the duration of the sweep so the
    operator gets a synchronous answer; that's why the rate budget is
    intentionally generous on the period (5 per 5 min, not per min).
    """
    workspace_ids = await pending_memory_svc.list_active_workspace_ids(db)
    visited = 0
    promoted = 0
    skipped = 0
    failed = 0
    for ws_id in workspace_ids:
        counts = await pending_memory_svc.promote_pending_memories_workspace_sweep(
            db, workspace_id=ws_id
        )
        visited += 1
        promoted += counts["promoted"]
        skipped += counts["skipped"]
        failed += counts["failed"]
    await audit_svc.record(
        db,
        action="admin.pending_memory.trigger",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="pending_memory",
        resource_id=None,
        summary=(
            f"manual sweep visited={visited} promoted={promoted} "
            f"skipped={skipped} failed={failed}"
        ),
        metadata={
            "workspaces_visited": visited,
            "promoted": promoted,
            "skipped": skipped,
            "failed": failed,
        },
        request=request,
    )
    await db.commit()
    return PromoteSweepResult(
        workspaces_visited=visited,
        promoted=promoted,
        skipped=skipped,
        failed=failed,
    )


# Keep MembershipRepository imported in case future code references the repo
# directly from this module (e.g. cross-workspace audit lookups).
_ = MembershipRepository
