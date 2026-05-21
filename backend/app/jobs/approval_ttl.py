"""Approval TTL processor — hourly cron (M2.5).

Two sweeps per tick, in this order so a row that crosses the 24h
horizon and the expiry boundary in the same tick gets exactly one
reminder before being closed:

1. **Pre-expiry reminder** — pending rows with
   ``expires_at <= now + 24h`` and ``reminder_sent = False``: emit one
   ``approval.expiring`` notification (M0.10 descriptor lives in
   ``services/notification_events.py``) and flip ``reminder_sent=True``
   so the next tick doesn't notify twice.
2. **Expired processor** — pending rows with ``expires_at <= now``:
   apply the TTL action prescribed by the roadmap *Approval TTL
   strategy* table:

   ================================  =======================================
   ``resource_type``                 TTL action
   ================================  =======================================
   ``skill_pack_archive``            **auto-execute** (run dispatch handler)
   every other recognised verb       **REJECT** (status → ``EXPIRED``)
   ``None`` (legacy tool-call)       **REJECT** (defensive — the runtime
                                     callback already handles its own 5-min
                                     timeout, this catches strays)
   ================================  =======================================

Both sweeps are workspace-scoped via the row itself; the cron job
walks every workspace's expired/expiring rows in a single pass and is
failure-isolated per row so one bad pack/flow can't take the whole
sweep down.

Cron slot: minute=22 (free against M0.7 minute={2,32} / M0.11
minute={0,5,…,55} / M0.3 minute=15 / M2.4 minute={7,37}). The slot is
explicitly chosen to leave the on-the-hour 5-min retention sweep
neighbour and the M0.3 judge backstop alone — see
``app/worker/arq_app.py`` cron table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services.approval import reject_approval
from app.services.approval_dispatch import (
    DispatchError,
    dispatch_approved_approval,
)
from app.services.notification_events import emit_event

log = logging.getLogger(__name__)

__all__ = [
    "AUDIT_EXPIRED_AUTO_EXECUTED",
    "AUDIT_EXPIRED_REJECTED",
    "AUDIT_EXPIRING_REMINDER_SENT",
    "AUDIT_TTL_FAILED_PERMANENT",
    "PROCESS_EXPIRED_APPROVALS_NAME",
    "on_approval_ttl_job_failed_permanent",
    "process_expired_approvals",
]


PROCESS_EXPIRED_APPROVALS_NAME = "process_expired_approvals"

AUDIT_EXPIRED_AUTO_EXECUTED = "approval.expired_auto_executed"
AUDIT_EXPIRED_REJECTED = "approval.expired_rejected"
AUDIT_EXPIRING_REMINDER_SENT = "approval.expiring_reminder_sent"
AUDIT_TTL_FAILED_PERMANENT = "approval.ttl_failed_permanent"


# Resource types for which expiry triggers an *automatic* apply
# instead of a rejection. Today: only the curator's archive proposal —
# that's the spirit of "if nobody objects in 7 days, archive the
# stale pack".
_AUTO_EXECUTE_ON_EXPIRY: frozenset[str] = frozenset(
    {ApprovalResourceType.SKILL_PACK_ARCHIVE.value}
)

# Pre-expiry reminder window. Hard-coded to 24h to match the roadmap
# TTL table; if we ever need it per-resource_type the workspace
# evolver settings are the right home for the override.
_REMINDER_LEAD = timedelta(hours=24)


# ─── Cron entrypoint ─────────────────────────────────────────
async def process_expired_approvals(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hourly TTL sweep — run reminder pass first, then expiry pass.

    Returns a JSON-serialisable summary so the operator can compare
    ticks in the audit feed without trawling logs. The two passes are
    isolated so a runaway dispatch on one row cannot prevent reminders
    from firing on the rest.
    """
    _ = ctx
    summary: dict[str, Any] = {
        "status": "ok",
        "expiring_seen": 0,
        "expiring_reminded": 0,
        "expired_seen": 0,
        "expired_auto_executed": 0,
        "expired_rejected": 0,
        "expired_errored": 0,
    }

    factory = get_session_factory()
    now = utcnow_naive()
    horizon = now + _REMINDER_LEAD

    # ── Pass 1: pre-expiry reminder ─────────────────────────
    async with factory() as db:
        reminder_rows = (
            (
                await db.execute(
                    select(Approval)
                    .where(Approval.status == ApprovalStatus.PENDING)
                    .where(Approval.expires_at.is_not(None))
                    .where(Approval.expires_at <= horizon)
                    .where(Approval.expires_at > now)
                    .where(Approval.reminder_sent.is_(False))
                )
            )
            .scalars()
            .all()
        )
        summary["expiring_seen"] = len(reminder_rows)

    for row in reminder_rows:
        try:
            sent = await _send_pre_expiry_reminder(approval_id=row.id)
            if sent:
                summary["expiring_reminded"] += 1
        except Exception:  # pragma: no cover - defensive
            log.exception(
                "approval.ttl reminder pass failed for approval=%s", row.id
            )

    # ── Pass 2: expired processor ───────────────────────────
    async with factory() as db:
        expired_rows = (
            (
                await db.execute(
                    select(Approval)
                    .where(Approval.status == ApprovalStatus.PENDING)
                    .where(Approval.expires_at.is_not(None))
                    .where(Approval.expires_at <= now)
                )
            )
            .scalars()
            .all()
        )
        summary["expired_seen"] = len(expired_rows)

    for row in expired_rows:
        try:
            outcome = await _process_expired(approval_id=row.id)
        except Exception:  # noqa: BLE001
            log.exception(
                "approval.ttl expiry pass failed for approval=%s", row.id
            )
            summary["expired_errored"] += 1
            continue
        if outcome == "auto_executed":
            summary["expired_auto_executed"] += 1
        elif outcome == "rejected":
            summary["expired_rejected"] += 1
        elif outcome == "errored":
            summary["expired_errored"] += 1

    return summary


# ─── Reminder helper ─────────────────────────────────────────
async def _send_pre_expiry_reminder(*, approval_id: uuid.UUID) -> bool:
    """Fan out the M0.10 ``approval.expiring`` event for one row.

    Returns True when the reminder was actually sent (not deduped /
    short-circuited). Best-effort — Redis / aux LLM unavailability is
    treated as a soft failure and the ``reminder_sent`` flag stays
    False so the next tick retries.
    """
    factory = get_session_factory()
    async with factory() as db:
        row = await _reload(db, approval_id=approval_id)
        if row is None or row.status != ApprovalStatus.PENDING:
            return False
        if row.reminder_sent:
            return False
        if row.expires_at is None:
            return False

        await emit_event(
            db,
            event_key="approval.expiring",
            workspace_id=row.workspace_id,
            actor_identity_id=None,
            cooldown_resource_id=str(row.id),
            payload={
                "approval_id": str(row.id),
                "resource_type": row.resource_type,
                "tool_name": row.tool_name,
                "expires_at": row.expires_at.isoformat(),
                "summary": row.summary,
                "action_url": f"/approvals?id={row.id}",
            },
        )

        row.reminder_sent = True
        await db.flush([row])
        await audit_svc.record(
            db,
            action=AUDIT_EXPIRING_REMINDER_SENT,
            actor_identity_id=None,
            workspace_id=row.workspace_id,
            resource_type="approval",
            resource_id=row.id,
            summary=(
                f"approval {row.id} expiring soon ({row.resource_type or row.tool_name})"
            ),
            metadata={
                "approval_id": str(row.id),
                "resource_type": row.resource_type,
                "tool_name": row.tool_name,
                "expires_at": row.expires_at.isoformat(),
            },
        )
        await db.commit()
        return True


# ─── Expiry helper ───────────────────────────────────────────
async def _process_expired(*, approval_id: uuid.UUID) -> str:
    """Apply the TTL action for one expired row.

    Returns one of ``"auto_executed"``, ``"rejected"``, ``"errored"``.
    """
    factory = get_session_factory()
    async with factory() as db:
        row = await _reload(db, approval_id=approval_id)
        if row is None or row.status != ApprovalStatus.PENDING:
            return "rejected"  # already moved by another tick

        rt = row.resource_type
        # Curator's auto-archive: nobody objected in 7 days → execute.
        if rt is not None and rt in _AUTO_EXECUTE_ON_EXPIRY:
            try:
                dispatch_result = await dispatch_approved_approval(
                    db,
                    approval=row,
                    actor_identity_id=None,
                )
            except DispatchError:
                # Dispatch wrote ``approval.dispatch_failed`` audit on
                # its own session before raising; we still need to
                # close the row so it doesn't loop forever. Mark as
                # rejected with a TTL-fail reason.
                await db.rollback()
                await _reject_with_audit(
                    approval_id=approval_id,
                    reason=(
                        "ttl auto-execute failed — see "
                        "approval.dispatch_failed audit"
                    ),
                )
                return "errored"
            # On success, finalise the row + audit + commit.
            row.status = ApprovalStatus.APPROVED
            row.decided_at = utcnow_naive()
            row.decided_reason = "ttl auto-execute"
            await db.flush([row])
            await audit_svc.record(
                db,
                action=AUDIT_EXPIRED_AUTO_EXECUTED,
                actor_identity_id=None,
                workspace_id=row.workspace_id,
                resource_type="approval",
                resource_id=row.id,
                summary=(
                    f"approval {row.id} auto-executed on expiry "
                    f"({rt}, applied={dispatch_result.applied_object_id if dispatch_result else None})"
                ),
                metadata={
                    "approval_id": str(row.id),
                    "resource_type": rt,
                    "applied_object_id": (
                        str(dispatch_result.applied_object_id)
                        if dispatch_result and dispatch_result.applied_object_id
                        else None
                    ),
                },
            )
            await db.commit()
            return "auto_executed"

    # Default path: REJECT. Use a fresh session because the previous
    # one has already been committed/rolled back above.
    await _reject_with_audit(
        approval_id=approval_id,
        reason="ttl expired — admin did not respond in time",
    )
    return "rejected"


async def _reject_with_audit(*, approval_id: uuid.UUID, reason: str) -> None:
    factory = get_session_factory()
    async with factory() as db:
        row = await _reload(db, approval_id=approval_id)
        if row is None or row.status != ApprovalStatus.PENDING:
            return
        rt = row.resource_type
        try:
            await reject_approval(
                db,
                approval_id=approval_id,
                workspace_id=row.workspace_id,
                actor_identity_id=None,
                reason=reason,
                status_override=ApprovalStatus.EXPIRED,
            )
        except Exception:  # pragma: no cover - defensive
            log.exception(
                "approval.ttl reject pass failed for approval=%s", approval_id
            )
            await db.rollback()
            return

        await audit_svc.record(
            db,
            action=AUDIT_EXPIRED_REJECTED,
            actor_identity_id=None,
            workspace_id=row.workspace_id,
            resource_type="approval",
            resource_id=row.id,
            summary=(
                f"approval {row.id} expired without decision "
                f"({rt or row.tool_name})"
            ),
            metadata={
                "approval_id": str(row.id),
                "resource_type": rt,
                "tool_name": row.tool_name,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            },
        )
        await db.commit()


async def _reload(db: Any, *, approval_id: uuid.UUID) -> Approval | None:
    return (
        await db.execute(select(Approval).where(Approval.id == approval_id))
    ).scalar_one_or_none()


# ─── ARQ permanent-failure hook ──────────────────────────────
async def on_approval_ttl_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """Three-strike hook for ``process_expired_approvals``.

    Mirrors the curator / pending-memory / evolver hooks: writes one
    stable audit row so operators can spot the dead-letter sweep
    without trawling Redis. Best-effort; never re-raises.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_TTL_FAILED_PERMANENT,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(
                    f"process_expired_approvals failed permanently: {exc!r}"
                ),
                metadata={
                    "function": str(
                        ctx.get("function") or PROCESS_EXPIRED_APPROVALS_NAME
                    ),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_approval_ttl_job_failed_permanent hook crashed")
