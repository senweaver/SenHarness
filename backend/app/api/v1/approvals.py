"""Approvals REST API — list pending + out-of-band decision.

When a pending approval row is decided via this endpoint (e.g. from a Slack
button / admin console / mobile app rather than the live WebSocket) the
in-process ``APPROVAL_MANAGER`` is notified immediately so the parked tool call
inside the kernel proceeds.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import AppError
from app.db.models.approval import ApprovalStatus
from app.repositories.approval import ApprovalRepository
from app.schemas._base import PagedResponse
from app.schemas.approval import (
    ApprovalDecision,
    ApprovalDecisionResponse,
    ApprovalRead,
    BulkApprovalDecision,
    BulkDecisionItem,
    BulkDecisionResult,
    DispatchResultRead,
)
from app.services import approval as approval_svc
from app.services import audit as audit_svc
from app.services import notifications as notif_svc
from app.services import permissions as perm
from app.services import workspace as ws_svc
from app.services.approval_dispatch import DispatchError

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=PagedResponse[ApprovalRead])
async def list_approvals(
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
    status_filter: str | None = Query(default=None, alias="status"),
    session_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> PagedResponse[ApprovalRead]:
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    repo = ApprovalRepository(db)
    if status_filter == "pending":
        rows = await repo.list_pending(
            workspace_id=workspace_id, session_id=session_id, limit=limit
        )
    else:
        rows = await repo.list_recent(workspace_id=workspace_id, limit=limit)
        if session_id is not None:
            rows = [r for r in rows if r.session_id == session_id]

    # Filter rows the actor is allowed to see. ``view_all`` is the common case
    # (admin / operator / auditor) and short-circuits the DB lookups.
    if not perm.has_capability(membership, "approvals.view_all"):
        filtered: list = []
        for row in rows:
            if await perm.evaluate_approval_visibility(
                db, approval=row, actor_membership=membership
            ):
                filtered.append(row)
        rows = filtered

    items = await _serialize_with_departments(repo, workspace_id=workspace_id, rows=rows)
    return PagedResponse(
        items=items,
        total=len(items),
        limit=limit,
        offset=0,
    )


async def _serialize_with_departments(
    repo: ApprovalRepository,
    *,
    workspace_id: uuid.UUID,
    rows: list,
) -> list[ApprovalRead]:
    """Bulk-resolve requester/decider department names and attach to DTOs.

    Pulled out so both the paged list and the ``/urgent`` preview route share
    a single department lookup per request (instead of one join per row).
    """
    ident_ids: list[uuid.UUID] = []
    for row in rows:
        if row.requested_by_identity_id:
            ident_ids.append(row.requested_by_identity_id)
        if row.decided_by_identity_id:
            ident_ids.append(row.decided_by_identity_id)
    dept_map = await repo.department_names_for_identities(
        workspace_id=workspace_id, identity_ids=ident_ids
    )
    out: list[ApprovalRead] = []
    for row in rows:
        card = ApprovalRead.model_validate(row)
        if row.requested_by_identity_id:
            card.requester_department_name = dept_map.get(row.requested_by_identity_id)
        if row.decided_by_identity_id:
            card.decided_by_department_name = dept_map.get(row.decided_by_identity_id)
        out.append(card)
    return out


@router.get("/urgent", response_model=list[ApprovalRead])
async def list_urgent_approvals(
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
    limit: int = Query(default=5, ge=1, le=20),
) -> list[ApprovalRead]:
    """Top-N pending approvals sorted by urgency (earliest expiry first).

    Powers the sidebar bell preview: hovering the bell shows the 5 most
    imminent pending rows the actor can see, each clickable into the live
    queue page.
    """
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    repo = ApprovalRepository(db)
    # Fetch with a wider cap, then apply visibility filter, then trim. This
    # keeps "top 5 soonest-expiring VISIBLE to the caller" accurate even when
    # the caller is not a workspace admin.
    raw = await repo.list_urgent_pending(workspace_id=workspace_id, limit=max(limit * 10, 50))
    if perm.has_capability(membership, "approvals.view_all"):
        rows = raw
    else:
        rows = []
        for row in raw:
            if await perm.evaluate_approval_visibility(
                db, approval=row, actor_membership=membership
            ):
                rows.append(row)
    rows = rows[:limit]
    return await _serialize_with_departments(repo, workspace_id=workspace_id, rows=rows)


@router.get("/counts")
async def approvals_counts(
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> dict:
    """Pending-approval counter for sidebar badges.

    Returns the count of pending rows **visible to the caller** (admins see
    everything, members see what they requested or what belongs to them).
    Members without ``approvals.view_all`` capability still get an accurate
    badge for their own stuff.
    """
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    repo = ApprovalRepository(db)
    # ``view_all`` capability short-circuits the filter — one SQL count.
    if perm.has_capability(membership, "approvals.view_all"):
        return {"pending": await repo.count_pending(workspace_id=workspace_id)}

    # Otherwise re-use the visibility predicate row-by-row. Pending queues
    # are typically small (<200 per workspace at any given moment) so this
    # is fine; if it grows we'll push the predicate into SQL.
    rows = await repo.list_pending(workspace_id=workspace_id, limit=500)
    visible = 0
    for row in rows:
        if await perm.evaluate_approval_visibility(db, approval=row, actor_membership=membership):
            visible += 1
    return {"pending": visible}


@router.post("/{approval_id}/decision", response_model=ApprovalDecisionResponse)
async def decide_approval(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
    request: Request,
) -> ApprovalDecisionResponse:
    """Approve or deny one approval.

    M2.5 — when ``action == "approve"`` and the row carries a
    ``resource_type``, the M2.5 dispatch handler runs **before** the
    status flip so the side effect (activate version, archive pack,
    create flow, …) is atomic with the decision. A failed dispatch
    rolls the whole transaction back, leaves the row pending, and
    surfaces a 409 with ``code='approval.dispatch_failed'``.
    """
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    repo = ApprovalRepository(db)

    # Load row first so we can run permission checks against it.
    row = await repo.get(approval_id)
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found.")

    # Capability + scope rule check.
    matched_rule = await perm.require_decide_approval(db, approval=row, actor_membership=membership)

    approved = payload.action == "approve"
    dispatch_result = None
    if approved:
        try:
            outcome = await approval_svc.approve_approval(
                db,
                approval_id=approval_id,
                workspace_id=workspace_id,
                actor_identity_id=identity_id,
                reason=payload.reason,
            )
        except DispatchError as e:
            # ``dispatch_approved_approval`` already wrote the
            # ``approval.dispatch_failed`` audit row on the same
            # session. Rolling back here drops it; record a fresh
            # one on a separate session before we surface the 409.
            await db.rollback()
            await _audit_dispatch_failure_external(
                approval_id=approval_id,
                workspace_id=workspace_id,
                actor_identity_id=identity_id,
                error=e,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": e.code or "approval.dispatch_failed",
                    "message": str(e.detail or e),
                    "extras": getattr(e, "extras", {}) or {},
                },
            ) from e
        except AppError as e:
            await db.rollback()
            raise HTTPException(
                status_code=e.status_code, detail={"code": e.code, "message": e.detail}
            ) from e
        row = outcome.approval
        if outcome.dispatch is not None:
            dispatch_result = DispatchResultRead(
                approval_id=outcome.dispatch.approval_id,
                resource_type=outcome.dispatch.resource_type,
                resource_id=outcome.dispatch.resource_id,
                applied_object_id=outcome.dispatch.applied_object_id,
                audit_action=outcome.dispatch.audit_action,
            )
    else:
        try:
            row = await approval_svc.reject_approval(
                db,
                approval_id=approval_id,
                workspace_id=workspace_id,
                actor_identity_id=identity_id,
                reason=payload.reason,
            )
        except AppError as e:
            await db.rollback()
            raise HTTPException(
                status_code=e.status_code, detail={"code": e.code, "message": e.detail}
            ) from e

    await audit_svc.record(
        db,
        action="approval.decide",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="approval",
        resource_id=row.id,
        summary=(
            f"{'approved' if approved else 'denied'} "
            f"{row.resource_type or row.tool_name!r}"
            + (f" for session {row.session_id}" if row.session_id else "")
        ),
        metadata={
            "tool_name": row.tool_name,
            "resource_type": row.resource_type,
            "decision": "approve" if approved else "deny",
            "reason": payload.reason,
            "session_id": str(row.session_id) if row.session_id else None,
            "dispatch_audit_action": (dispatch_result.audit_action if dispatch_result else None),
            "applied_object_id": (
                str(dispatch_result.applied_object_id)
                if dispatch_result and dispatch_result.applied_object_id
                else None
            ),
        },
        request=request,
    )
    if row.requested_by_identity_id and row.requested_by_identity_id != identity_id:
        await notif_svc.create_notification(
            db,
            workspace_id=workspace_id,
            recipient_identity_id=row.requested_by_identity_id,
            actor_identity_id=identity_id,
            kind="approval.decided",
            level="success" if approved else "warning",
            title=f"审批已{'通过' if approved else '拒绝'}",
            body=f"工具 {row.tool_name} 的审批已{'通过' if approved else '拒绝'}",
            resource_type="approval",
            resource_id=row.id,
            action_url=f"/approvals?id={row.id}",
            metadata_json={"decision": "approve" if approved else "deny"},
        )
    await db.commit()

    # Wake up the parked tool call (no-op if the runtime is elsewhere).
    await APPROVAL_MANAGER.decide(
        approval_id,
        approved=approved,
        reason=payload.reason,
        decided_by=identity_id,
    )
    log_extra = {"matched_rule": matched_rule}
    _ = log_extra  # keep for tracing in P5
    serialized = await _serialize_with_departments(repo, workspace_id=workspace_id, rows=[row])
    return ApprovalDecisionResponse(
        approval=serialized[0],
        dispatch_result=dispatch_result,
    )


async def _audit_dispatch_failure_external(
    *,
    approval_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    error: DispatchError,
) -> None:
    """Persist the dispatch-failure audit on a fresh session.

    The original audit row landed on the same session that just rolled
    back, so we re-audit on a fresh session for durability. Best
    effort — never lets an audit failure swallow the original error.
    """
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as fresh:
            await audit_svc.record(
                fresh,
                action="approval.dispatch_failed",
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="approval",
                resource_id=approval_id,
                summary=(f"approval {approval_id} dispatch failed during approve"),
                metadata={
                    "approval_id": str(approval_id),
                    "code": error.code,
                    "error_repr": repr(error)[:500],
                    "extras": getattr(error, "extras", {}) or {},
                },
            )
            await fresh.commit()
    except Exception:  # pragma: no cover - audit best-effort
        pass


@router.post("/bulk-decision", response_model=BulkDecisionResult)
async def bulk_decide_approvals(
    payload: BulkApprovalDecision,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
    request: Request,
) -> BulkDecisionResult:
    """Decide a batch of approvals in one request.

    Each approval is processed independently — the response describes exactly
    which ids succeeded and which failed (with a machine-readable error code).
    The endpoint never rolls back partial progress because every decision is
    already atomic at the row level, and blocking the *entire* batch on one
    bad id would be worse UX than surfacing per-row errors.

    Error codes returned in ``failed[].error_code``:

    * ``not_found``          — row doesn't exist or belongs to another workspace
    * ``already_decided``    — row is no longer pending
    * ``no_permission``      — caller can't decide this specific row
    * ``internal``            — unexpected exception (logged server-side)
    """
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    repo = ApprovalRepository(db)
    approved = payload.action == "approve"

    # Dedupe while preserving order so callers don't double-count.
    seen: set[uuid.UUID] = set()
    unique_ids: list[uuid.UUID] = []
    for aid in payload.approval_ids:
        if aid in seen:
            continue
        seen.add(aid)
        unique_ids.append(aid)

    succeeded: list[uuid.UUID] = []
    failed: list[BulkDecisionItem] = []
    # Collect successful rows for a single audit summary at the end.
    decided_tools: list[str] = []
    decided_rows: list = []

    for aid in unique_ids:
        row = await repo.get(aid)
        if row is None or row.workspace_id != workspace_id:
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code="not_found",
                    error_message="Approval not found.",
                )
            )
            continue
        if row.status != ApprovalStatus.PENDING:
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code="already_decided",
                    error_message=f"Already {row.status.value}.",
                )
            )
            continue

        decision = await perm.evaluate_approval_decision(
            db, approval=row, actor_membership=membership
        )
        if not decision.allowed:
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code="no_permission",
                    error_message="No permission to decide this approval.",
                )
            )
            continue

        # M2.5 — wrap each row in a savepoint so a dispatch failure on
        # one row doesn't roll back the rest of the batch. Approves
        # also call ``dispatch_approved_approval`` so the apply step
        # stays atomic with the status flip per-row.
        try:
            async with db.begin_nested():
                if approved:
                    outcome = await approval_svc.approve_approval(
                        db,
                        approval_id=aid,
                        workspace_id=workspace_id,
                        actor_identity_id=identity_id,
                        reason=payload.reason,
                    )
                    decided = outcome.approval
                else:
                    decided = await approval_svc.reject_approval(
                        db,
                        approval_id=aid,
                        workspace_id=workspace_id,
                        actor_identity_id=identity_id,
                        reason=payload.reason,
                    )
        except DispatchError as e:
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code=e.code or "dispatch_failed",
                    error_message=str(e.detail or e),
                )
            )
            continue
        except AppError as e:  # pragma: no cover — defensive
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code=e.code or "internal",
                    error_message=str(e.detail or e),
                )
            )
            continue
        except Exception:  # pragma: no cover — defensive
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code="internal",
                    error_message="Decide failed; see server logs.",
                )
            )
            continue

        if decided is None:
            failed.append(
                BulkDecisionItem(
                    approval_id=aid,
                    ok=False,
                    error_code="not_found",
                    error_message="Approval not found.",
                )
            )
            continue

        succeeded.append(aid)
        decided_tools.append(decided.tool_name)
        decided_rows.append(decided)

    # Commit once for the whole batch — ApprovalRepository.decide only
    # flushes, the outer boundary owns the commit.
    await db.commit()

    # Signal each parked runner AFTER the commit so the DB row is visible if
    # the callback re-reads it.
    for aid in succeeded:
        await APPROVAL_MANAGER.decide(
            aid,
            approved=approved,
            reason=payload.reason,
            decided_by=identity_id,
        )

    for decided in decided_rows:
        if not decided.requested_by_identity_id or decided.requested_by_identity_id == identity_id:
            continue
        await notif_svc.create_notification(
            db,
            workspace_id=workspace_id,
            recipient_identity_id=decided.requested_by_identity_id,
            actor_identity_id=identity_id,
            kind="approval.decided",
            level="success" if approved else "warning",
            title=f"审批已{'通过' if approved else '拒绝'}",
            body=f"工具 {decided.tool_name} 的审批已{'通过' if approved else '拒绝'}",
            resource_type="approval",
            resource_id=decided.id,
            action_url=f"/approvals?id={decided.id}",
            metadata_json={"decision": "approve" if approved else "deny"},
        )

    # Single audit line covering the whole batch outcome.
    if succeeded:
        await audit_svc.record(
            db,
            action="approval.bulk_decide",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="approval",
            resource_id=None,
            summary=(
                f"{'approved' if approved else 'denied'} {len(succeeded)} approval(s) in bulk"
            ),
            metadata={
                "decision": "approve" if approved else "deny",
                "reason": payload.reason,
                "succeeded_count": len(succeeded),
                "failed_count": len(failed),
                "tool_names": decided_tools[:20],
            },
            request=request,
        )
        await db.commit()
    elif decided_rows:
        # Persist notifications even when no audit line is emitted.
        await db.commit()

    return BulkDecisionResult(succeeded=succeeded, failed=failed)
