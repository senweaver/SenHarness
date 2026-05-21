"""Cache-aware mutation service (M0.7).

Memory writes default to ``effective="next_session"`` so the system
prompt that already lives in provider cache stays valid through the
end of the current run. The mutation lands in
``pending_memories`` with status ``PENDING`` and is promoted to its
target table either:

* synchronously, by the post-FINAL hook in
  :mod:`app.api.v1.sessions` (and the channel/flow mirror in
  :mod:`app.services.agent_runner`); or
* asynchronously, by :func:`promote_pending_memories_workspace_sweep`
  scheduled from the ARQ cron — backstop for runs that ended without
  the synchronous hook firing (backend crash, websocket abrupt close).

A workspace can opt back into immediate writes by setting
``home_config_json["memory"]["allow_immediate"] = True``; an agent
that requests ``effective="now"`` against a workspace where the gate
is closed receives a structured rejection (``ImmediateMemoryNotPermitted``)
that the tool wrapper translates to a tool-result, **not** a hard
error — the run continues.

This module also enforces the always-on hard cap (design principle 3)
by delegating to :func:`app.services.memory.apply_payload`, which
raises :class:`MemoryHardCapExceeded` before the row lands in
``memories``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ImmediateMemoryNotPermitted,
    MemoryHardCapExceeded,
    ValidationFailed,
)
from app.core.security import utcnow_naive
from app.db.models.pending_memory import (
    PendingMemory,
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.repositories.pending_memory import PendingMemoryRepository
from app.services import audit as audit_svc
from app.services import memory as memory_svc

log = logging.getLogger(__name__)


# Sessions inactive for at least this many minutes are considered
# "stale" by the workspace sweep — anything more recent is left for
# the synchronous capture hook to drain so the two paths don't race
# on the same row.
_SWEEP_SESSION_QUIET_MINUTES = 30


# ─── Queue path ──────────────────────────────────────────────────
async def queue_pending_memory(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    target_table: PendingMemoryTargetTable,
    payload: dict,
) -> PendingMemory:
    """Persist a pending row + emit ``pending_memory.queued``.

    Payload schema validation is delegated to the per-target apply
    helper (``memory.apply_payload`` for ``MEMORIES``); for now we do
    a permissive contract check here so the queue accepts everything
    the apply helper would also accept, and rejects only obvious
    schema breaks (missing ``content`` for the memories case).
    """
    _validate_payload(target_table, payload)

    repo = PendingMemoryRepository(db)
    row = await repo.create(
        workspace_id=workspace_id,
        session_id=session_id,
        identity_id=identity_id,
        target_table=target_table.value,
        payload=dict(payload),
        status=PendingMemoryStatus.PENDING.value,
    )
    await audit_svc.record(
        db,
        action="pending_memory.queued",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="pending_memory",
        resource_id=row.id,
        summary=f"queued {target_table.value} write for next-session promote",
        metadata={
            "session_id": str(session_id),
            "target_table": target_table.value,
        },
    )
    return row


def _validate_payload(
    target_table: PendingMemoryTargetTable, payload: dict
) -> None:
    if not isinstance(payload, dict):
        raise ValidationFailed(
            "pending_memory_payload_invalid",
            code="pending_memory.payload_invalid",
        )
    if target_table == PendingMemoryTargetTable.MEMORIES:
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValidationFailed(
                "pending_memory_payload_missing_content",
                code="pending_memory.payload_invalid",
            )
        scope = payload.get("scope", "user")
        if scope not in {"user", "assistant", "workspace"}:
            raise ValidationFailed(
                "pending_memory_payload_invalid_scope",
                code="pending_memory.payload_invalid",
            )


async def queue_immediate_or_pending(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    target_table: PendingMemoryTargetTable,
    payload: dict,
    effective: Literal["next_session", "now"] = "next_session",
) -> tuple[PendingMemory | None, dict | None]:
    """Single entry point used by the agent ``memorize`` tool.

    * ``effective="next_session"`` (default) — queue the write and
      audit ``memory.deferred_to_next_session``. Returns
      ``(pending_row, None)``.
    * ``effective="now"`` — check the workspace ``allow_immediate``
      gate; if the gate is closed raise
      :class:`ImmediateMemoryNotPermitted` (the tool wrapper turns
      that into a structured rejection). If the gate is open, apply
      the payload immediately, audit ``memory.applied_immediate``, and
      return ``(None, applied_record_dict)``.

    Hard-cap and scope-policy violations from
    :func:`memory.apply_payload` propagate as
    :class:`MemoryHardCapExceeded` / :class:`ValidationFailed` so the
    tool wrapper can translate them into the appropriate tool result.
    """
    _validate_payload(target_table, payload)

    if effective == "now":
        settings = await memory_svc.get_workspace_memory_settings(
            db, workspace_id=workspace_id
        )
        if not settings.allow_immediate:
            await audit_svc.record(
                db,
                action="memory.immediate_not_permitted",
                actor_identity_id=identity_id,
                workspace_id=workspace_id,
                resource_type="pending_memory",
                resource_id=None,
                summary="agent attempted effective=now but workspace gate is closed",
                metadata={
                    "session_id": str(session_id),
                    "target_table": target_table.value,
                },
            )
            raise ImmediateMemoryNotPermitted(
                "memory_immediate_not_permitted",
                code="memory.immediate_not_permitted",
            )

        applied = await _apply_for_target(
            db,
            workspace_id=workspace_id,
            identity_id=identity_id,
            agent_id=agent_id,
            target_table=target_table,
            payload=payload,
        )
        await audit_svc.record(
            db,
            action="memory.applied_immediate",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type=target_table.value,
            resource_id=uuid.UUID(applied["id"]) if applied.get("id") else None,
            summary="immediate memory write (workspace gate open)",
            metadata={
                "session_id": str(session_id),
                "target_table": target_table.value,
            },
        )
        return None, applied

    pending = await queue_pending_memory(
        db,
        workspace_id=workspace_id,
        session_id=session_id,
        identity_id=identity_id,
        target_table=target_table,
        payload=payload,
    )
    await audit_svc.record(
        db,
        action="memory.deferred_to_next_session",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="pending_memory",
        resource_id=pending.id,
        summary="memory deferred until next session boundary",
        metadata={
            "session_id": str(session_id),
            "target_table": target_table.value,
        },
    )
    return pending, None


# ─── Promote ────────────────────────────────────────────────────
async def promote_pending_memories_for_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Drain ``PENDING`` rows for the just-finished session.

    Idempotent: re-running with no new rows yields ``{"promoted": 0,
    "skipped": 0, "failed": 0}``. Hard-cap / scope violations terminate
    the row at ``SKIPPED``; transient apply errors set ``FAILED`` with
    ``failure_count`` bumped (the workspace sweep retries up to the
    platform ceiling).
    """
    settings = await memory_svc.get_workspace_memory_settings(
        db, workspace_id=workspace_id
    )
    repo = PendingMemoryRepository(db)
    rows = await repo.list_pending_for_session(
        workspace_id=workspace_id,
        session_id=session_id,
        limit=settings.promotion_max_per_session,
    )
    return await _promote_rows(
        db,
        repo=repo,
        rows=rows,
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
        max_failure_count=settings.max_failure_count_before_skip,
        trigger="capture_hook",
    )


async def promote_pending_memories_workspace_sweep(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    max_age_seconds: int = 1800,
) -> dict[str, int]:
    """Backstop sweep for sessions that ended without firing the hook.

    Two filters keep the sweep from racing the synchronous path:

    * ``created_at < now - max_age_seconds`` — only old-enough rows;
    * the parent session is either soft-deleted or has been quiet for
      at least :data:`_SWEEP_SESSION_QUIET_MINUTES`.

    Both filters apply because a slow chat (open browser, no traffic)
    must not have its pending writes promoted out from under the
    synchronous hook the next time the user replies.
    """
    settings = await memory_svc.get_workspace_memory_settings(
        db, workspace_id=workspace_id
    )
    repo = PendingMemoryRepository(db)
    cutoff = utcnow_naive() - timedelta(seconds=int(max_age_seconds))
    quiet_floor = utcnow_naive() - timedelta(
        minutes=_SWEEP_SESSION_QUIET_MINUTES
    )

    pending_rows = list(
        await repo.list_pending_for_workspace(
            workspace_id=workspace_id,
            status=PendingMemoryStatus.PENDING,
            older_than=cutoff,
            limit=settings.promotion_max_per_session,
        )
    )
    failed_rows = list(
        await repo.list_eligible_for_retry(
            workspace_id=workspace_id,
            max_failure_count=settings.max_failure_count_before_skip,
            older_than=cutoff,
            limit=settings.promotion_max_per_session,
        )
    )
    candidates = pending_rows + failed_rows
    if not candidates:
        return {"promoted": 0, "skipped": 0, "failed": 0}

    eligible: list[PendingMemory] = []
    for row in candidates:
        if await _session_is_quiet(
            db, session_id=row.session_id, quiet_floor=quiet_floor
        ):
            if row.status == PendingMemoryStatus.FAILED:
                await repo.reset_failed_to_pending(pending=row)
            eligible.append(row)
    if not eligible:
        return {"promoted": 0, "skipped": 0, "failed": 0}
    return await _promote_rows(
        db,
        repo=repo,
        rows=eligible,
        workspace_id=workspace_id,
        actor_identity_id=None,
        max_failure_count=settings.max_failure_count_before_skip,
        trigger="workspace_sweep",
    )


async def _session_is_quiet(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    quiet_floor: datetime,
) -> bool:
    """True when the session is soft-deleted, or has been quiet long
    enough to be safe to drain. Sessions rows missing entirely (FK
    cascade fired, but the pending row hasn't been swept) are also
    considered quiet — there is nothing left to race against.
    """
    row = (
        await db.execute(
            text(
                "SELECT deleted_at, last_message_at FROM sessions WHERE id = :sid"
            ),
            {"sid": str(session_id)},
        )
    ).first()
    if row is None:
        return True
    deleted_at, last_message_at = row[0], row[1]
    if deleted_at is not None:
        return True
    if last_message_at is None:
        return True
    return last_message_at < quiet_floor


async def _promote_rows(
    db: AsyncSession,
    *,
    repo: PendingMemoryRepository,
    rows: Sequence[PendingMemory],
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    max_failure_count: int,
    trigger: str,
) -> dict[str, int]:
    promoted = 0
    skipped = 0
    failed = 0
    for row in rows:
        target_table = _target_from_value(row.target_table)
        if target_table is None:
            await repo.mark_skipped(
                pending=row, reason="unknown_target_table"
            )
            await audit_svc.record(
                db,
                action="memory.promotion_failed",
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="pending_memory",
                resource_id=row.id,
                summary="unknown target_table on promote",
                metadata={
                    "session_id": str(row.session_id),
                    "target_table": str(row.target_table),
                    "trigger": trigger,
                },
            )
            skipped += 1
            continue

        try:
            applied = await _apply_for_target(
                db,
                workspace_id=workspace_id,
                identity_id=row.identity_id,
                agent_id=None,
                target_table=target_table,
                payload=row.payload or {},
            )
        except MemoryHardCapExceeded as exc:
            await repo.mark_skipped(
                pending=row, reason="hard_cap_exceeded"
            )
            await audit_svc.record(
                db,
                action="memory.hard_cap_blocked",
                actor_identity_id=row.identity_id,
                workspace_id=workspace_id,
                resource_type="pending_memory",
                resource_id=row.id,
                summary="memory write skipped — hard cap exceeded",
                metadata={
                    "session_id": str(row.session_id),
                    "target_table": target_table.value,
                    "extras": getattr(exc, "extras", {}),
                    "trigger": trigger,
                },
            )
            skipped += 1
            continue
        except ValidationFailed as exc:
            await repo.mark_skipped(
                pending=row, reason=getattr(exc, "code", "validation_failed")
            )
            await audit_svc.record(
                db,
                action="memory.promotion_failed",
                actor_identity_id=row.identity_id,
                workspace_id=workspace_id,
                resource_type="pending_memory",
                resource_id=row.id,
                summary="memory promote rejected by service validation",
                metadata={
                    "session_id": str(row.session_id),
                    "target_table": target_table.value,
                    "code": getattr(exc, "code", "validation_failed"),
                    "trigger": trigger,
                },
            )
            skipped += 1
            continue
        except Exception as exc:
            log.exception("pending_memory promote failed (row=%s)", row.id)
            await repo.mark_failed(
                pending=row, reason=type(exc).__name__
            )
            await audit_svc.record(
                db,
                action="memory.promotion_failed",
                actor_identity_id=row.identity_id,
                workspace_id=workspace_id,
                resource_type="pending_memory",
                resource_id=row.id,
                summary="memory promote raised — will retry until ceiling",
                metadata={
                    "session_id": str(row.session_id),
                    "target_table": target_table.value,
                    "error_class": type(exc).__name__,
                    "failure_count": int(row.failure_count or 0),
                    "trigger": trigger,
                },
            )
            failed += 1
            if int(row.failure_count or 0) >= max_failure_count:
                await repo.mark_skipped(
                    pending=row, reason="max_failure_count_exceeded"
                )
                # Net effect: failed bumped above, then collapsed to skipped.
                failed -= 1
                skipped += 1
            continue

        target_id_raw = applied.get("id") if isinstance(applied, dict) else None
        target_id = (
            uuid.UUID(target_id_raw)
            if isinstance(target_id_raw, str) and target_id_raw
            else None
        )
        await repo.mark_promoted(pending=row, target_id=target_id)
        await audit_svc.record(
            db,
            action="memory.promoted_from_pending",
            actor_identity_id=row.identity_id,
            workspace_id=workspace_id,
            resource_type=target_table.value,
            resource_id=target_id,
            summary="pending memory promoted to live table",
            metadata={
                "pending_memory_id": str(row.id),
                "session_id": str(row.session_id),
                "target_table": target_table.value,
                "trigger": trigger,
            },
        )
        promoted += 1
    return {"promoted": promoted, "skipped": skipped, "failed": failed}


def _target_from_value(value: Any) -> PendingMemoryTargetTable | None:
    if isinstance(value, PendingMemoryTargetTable):
        return value
    try:
        return PendingMemoryTargetTable(str(value))
    except ValueError:
        return None


async def _apply_for_target(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    target_table: PendingMemoryTargetTable,
    payload: dict,
) -> dict:
    if target_table == PendingMemoryTargetTable.MEMORIES:
        row = await memory_svc.apply_payload(
            db,
            workspace_id=workspace_id,
            identity_id=identity_id,
            agent_id=agent_id,
            payload=payload,
        )
        return {
            "id": str(row.id),
            "target_table": target_table.value,
            "scope": row.scope.value if hasattr(row.scope, "value") else str(row.scope),
            "kind": row.kind.value if hasattr(row.kind, "value") else str(row.kind),
            "key": row.key,
        }
    # SkillPack apply path is reserved for M1 — for now reject so the
    # row terminates at SKIPPED rather than silently disappearing.
    raise ValidationFailed(
        f"unsupported_target_table:{target_table.value}",
        code="pending_memory.target_unsupported",
    )


# ─── Cancel + Stats ─────────────────────────────────────────────
async def cancel_pending_memory(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pending_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> PendingMemory:
    """Flip a ``PENDING`` row to ``SKIPPED`` (reason ``user_cancelled``).

    Soft-cancel only — the row stays so the lineage from the agent's
    intent through to the cancellation decision remains auditable.
    """
    repo = PendingMemoryRepository(db)
    row = await repo.get(pending_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        from app.core.errors import NotFound

        raise NotFound(
            "pending_memory_not_found", code="pending_memory.not_found"
        )
    if row.status != PendingMemoryStatus.PENDING:
        # Idempotent: cancelling an already-promoted / skipped row is a no-op.
        return row
    await repo.mark_skipped(pending=row, reason="user_cancelled")
    await audit_svc.record(
        db,
        action="pending_memory.cancelled",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="pending_memory",
        resource_id=row.id,
        summary="pending memory cancelled by user",
        metadata={
            "session_id": str(row.session_id),
            "target_table": row.target_table.value
            if hasattr(row.target_table, "value")
            else str(row.target_table),
        },
    )
    return row


async def list_session_pending(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = 200,
    offset: int = 0,
) -> Sequence[PendingMemory]:
    return await PendingMemoryRepository(db).list_for_session(
        workspace_id=workspace_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


async def workspace_stats(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> dict[str, Any]:
    repo = PendingMemoryRepository(db)
    counts = await repo.workspace_status_counts(workspace_id=workspace_id)
    oldest = await repo.workspace_oldest_pending(workspace_id=workspace_id)
    return {
        "workspace_id": workspace_id,
        "pending": counts.get(PendingMemoryStatus.PENDING.value, 0),
        "promoted": counts.get(PendingMemoryStatus.PROMOTED.value, 0),
        "skipped": counts.get(PendingMemoryStatus.SKIPPED.value, 0),
        "failed": counts.get(PendingMemoryStatus.FAILED.value, 0),
        "oldest_pending_at": oldest,
    }


async def list_active_workspace_ids(
    db: AsyncSession, *, since: datetime | None = None, limit: int = 500
) -> Sequence[uuid.UUID]:
    return await PendingMemoryRepository(db).list_active_workspace_ids(
        since=since, limit=limit
    )
