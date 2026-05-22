"""Approval service — persistence + runtime integration.

Exposes:
- ``make_approval_callback(...)``: returns an async ``(tool_name, args) -> bool``
  suitable for ``pydantic_ai_shields.ToolGuard(approval_callback=...)``.
  Each call creates a DB row, registers a pending future on the
  ``ApprovalManager``, waits for the decision (with timeout), updates the row,
  and returns the boolean outcome.

- ``approve_approval(...)`` / ``reject_approval(...)`` (M2.5): unified
  service entry-points used by the REST decision endpoint. ``approve`` runs
  the M2.5 dispatch handler before flipping the row to APPROVED so the
  side effect (activate version, archive pack, create flow, …) and the
  status transition are atomic — if dispatch raises the row stays
  ``pending`` and the admin sees the error.

- ``decide(...)``: kept as the low-level repository wrapper used by the
  in-runtime tool-call path; new callers should prefer the typed
  ``approve_approval`` / ``reject_approval`` helpers.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.approvals import APPROVAL_MANAGER, ApprovalCallback
from app.core.errors import AppError, NotFound
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.session import get_session_factory
from app.repositories.approval import ApprovalRepository
from app.services.approval_dispatch import (
    DispatchError,
    DispatchResult,
    dispatch_approved_approval,
)

log = logging.getLogger(__name__)


__all__ = [
    "ApprovalNotPending",
    "ApproveOutcome",
    "approve_approval",
    "make_approval_callback",
    "reject_approval",
]


class ApprovalNotPending(AppError):
    """Caller tried to decide an approval that is no longer pending."""

    code = "approval.not_pending"
    default_status = 409


def make_approval_callback(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    requested_by_identity_id: uuid.UUID | None,
    ttl_seconds: int = 300,
    extra: dict[str, Any] | None = None,
) -> ApprovalCallback:
    """Build the callback injected into ``ToolGuard``.

    The callback is stateless — it uses the module-level ``APPROVAL_MANAGER``
    and opens a fresh DB session per request so it doesn't leak the runner's
    DB session across approval waits (which could be minutes long).
    """

    async def callback(tool_name: str, args: dict[str, Any]) -> bool:
        approval_id = uuid.uuid4()
        # 1) Persist pending row.
        try:
            async with get_session_factory()() as db:
                repo = ApprovalRepository(db)
                row = await repo.create(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    tool_args=_safe_jsonify(args),
                    summary=_compose_summary(tool_name, args),
                    requested_by_identity_id=requested_by_identity_id,
                    expires_at=utcnow_naive() + timedelta(seconds=ttl_seconds),
                )
                approval_id = row.id
                await db.commit()
        except Exception:
            log.exception("failed to persist approval row; using in-memory only")

        # 2) Register in-memory pending future.
        await APPROVAL_MANAGER.register(
            approval_id=approval_id,
            session_id=session_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            tool_args=_safe_jsonify(args),
            summary=_compose_summary(tool_name, args),
            ttl=timedelta(seconds=ttl_seconds),
            extra=extra or {},
        )

        # 3) Wait.
        approved, timed_out = await APPROVAL_MANAGER.wait(approval_id, timeout_s=ttl_seconds + 5)

        # 4) Persist decision (if not already written by the decide endpoint).
        #    Timeout path writes status=EXPIRED explicitly so the audit feed
        #    distinguishes stale requests from user-denied ones.
        try:
            async with get_session_factory()() as db:
                repo = ApprovalRepository(db)
                row = await repo.decide(
                    approval_id=approval_id,
                    workspace_id=workspace_id,
                    approved=approved,
                    reason="timeout" if timed_out else None,
                    decided_by_identity_id=None,
                    now=utcnow_naive(),
                    status_override=(ApprovalStatus.EXPIRED if timed_out else None),
                )
                if row is not None and row.status == ApprovalStatus.PENDING:
                    # Decide moved it to approved/denied — if it's still pending
                    # here that means the row was already decided by someone
                    # else (WS/REST), which is the happy path.
                    pass
                await db.commit()
        except Exception:
            log.exception("failed to persist approval decision")

        log.info(
            "approval %s %s (tool=%s approved=%s timed_out=%s)",
            approval_id,
            "expired" if timed_out else ("approved" if approved else "denied"),
            tool_name,
            approved,
            timed_out,
        )
        return approved

    return callback


def _safe_jsonify(args: dict[str, Any]) -> dict[str, Any]:
    """Keep approval payloads compact + JSON-safe; stringify oddities."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) else v[:2000]
        elif isinstance(v, (list, dict)):
            try:
                import json

                out[k] = json.loads(json.dumps(v, default=str))
            except Exception:
                out[k] = str(v)[:2000]
        else:
            out[k] = str(v)[:2000]
    return out


def _compose_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Short human-readable line we show in the approval card title."""
    if tool_name == "execute":
        cmd = str(args.get("command", ""))[:120]
        return f"$ {cmd}"
    if tool_name in ("write_file", "edit_file"):
        path = str(args.get("path", args.get("file_path", "?")))
        return f"{tool_name} → {path}"
    if tool_name == "delete_file":
        return f"delete {args.get('path') or args.get('file_path')}"
    kv = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:4])
    return f"{tool_name}({kv})"


# ─── M2.5: typed approve/reject service entry-points ────────
class ApproveOutcome:
    """Result envelope returned by :func:`approve_approval`.

    ``approval`` is the DB row after the status flip; ``dispatch``
    is the dispatch handler's :class:`DispatchResult` or ``None`` when
    the row is a legacy tool-call approval (no ``resource_type``) or
    a recognised-but-not-yet-dispatched verb (M3 hub_promotion etc.).

    Attribute, not dataclass, so adding fields downstream doesn't break
    pickle/serialisation in callers using ``__dict__``.
    """

    __slots__ = ("approval", "dispatch")

    def __init__(self, approval: Approval, dispatch: DispatchResult | None) -> None:
        self.approval = approval
        self.dispatch = dispatch


async def approve_approval(
    db: AsyncSession,
    *,
    approval_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str | None = None,
) -> ApproveOutcome:
    """Mark ``approval_id`` approved + run M2.5 dispatch handler.

    Order of operations:

    1. Load the row (404 on missing or wrong workspace).
    2. Refuse if not pending (:class:`ApprovalNotPending`).
    3. Run :func:`dispatch_approved_approval` — on
       :class:`DispatchError` we re-raise so the caller can roll back
       the surrounding transaction and surface a 409 with the stable
       ``code``. On unexpected exception we wrap it in DispatchError
       to preserve the rollback contract.
    4. Flip status → APPROVED via the repository.

    Caller owns the commit. ``reason`` lands on the row's
    ``decided_reason`` column (the audit + notification rows are still
    written by the API layer to keep this service free of HTTP context).
    """
    repo = ApprovalRepository(db)
    row = await repo.get(approval_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("approval not found", code="approval.not_found")
    if row.status != ApprovalStatus.PENDING:
        raise ApprovalNotPending(
            f"approval already {row.status.value}",
            code="approval.not_pending",
            extras={"current_status": row.status.value},
        )

    # Dispatch BEFORE the status flip so a failed apply leaves the row
    # pending and the admin can retry. ``dispatch_approved_approval``
    # raises :class:`DispatchError` on internal failure; the API
    # caller is expected to roll back the surrounding transaction /
    # savepoint and re-record the durable
    # ``approval.dispatch_failed`` audit on a fresh session.
    dispatch_result = await dispatch_approved_approval(
        db, approval=row, actor_identity_id=actor_identity_id
    )

    decided = await repo.decide(
        approval_id=approval_id,
        workspace_id=workspace_id,
        approved=True,
        reason=reason,
        decided_by_identity_id=actor_identity_id,
        now=utcnow_naive(),
    )
    if decided is None:
        # Should not happen — we already loaded the row above. Guard
        # against the race where another worker decided in the gap.
        raise NotFound("approval not found", code="approval.not_found")
    return ApproveOutcome(approval=decided, dispatch=dispatch_result)


async def reject_approval(
    db: AsyncSession,
    *,
    approval_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str | None = None,
    status_override: ApprovalStatus | None = None,
) -> Approval:
    """Mark ``approval_id`` denied (or expired, with ``status_override``).

    Most reject paths just record the decision and exit. M2.5.1 added
    one side effect: rejecting a ``subagent_hallucination_review``
    transitions the parked SubAgentRun to ``KILLED`` so the parent
    runner can observe the cancel via the next heartbeat tick. The
    TTL processor calls this with ``status_override=EXPIRED`` so the
    audit feed distinguishes admin-denied from time-expired rows.
    Caller commits.
    """
    repo = ApprovalRepository(db)
    row = await repo.get(approval_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("approval not found", code="approval.not_found")
    if row.status != ApprovalStatus.PENDING:
        raise ApprovalNotPending(
            f"approval already {row.status.value}",
            code="approval.not_pending",
            extras={"current_status": row.status.value},
        )

    # Side effect must run BEFORE the row is closed so the spine row
    # transitions inside the same logical commit. apply_hallucination_decision
    # is a no-op when the spine row is already terminal (race against
    # the reaper) so this stays safe across all reject sources.
    if row.resource_type == "subagent_hallucination_review":
        from app.services import subagent_run as subagent_svc

        await subagent_svc.apply_hallucination_decision(
            db,
            approval=row,
            approved=False,
            actor_identity_id=actor_identity_id,
        )

    decided = await repo.decide(
        approval_id=approval_id,
        workspace_id=workspace_id,
        approved=False,
        reason=reason,
        decided_by_identity_id=actor_identity_id,
        now=utcnow_naive(),
        status_override=status_override,
    )
    if decided is None:
        raise NotFound("approval not found", code="approval.not_found")
    return decided


# Re-export DispatchError so callers don't have to know the
# approval_dispatch module exists.
__all__.append("DispatchError")
_ = DispatchError  # silence unused-import lint when re-exported above
