"""Session goal lock service (M0.1).

Owns the lifecycle of an active goal per chat session plus the per-message
alignment scores produced by the async judge. Every write goes through
the audit log so the workspace timeline shows lock / unlock / threshold
edits, and the GDPR cascade hook (M0.11) sees every row it has to wipe.

Multi-tenancy: every read filters on ``workspace_id`` so a leaked goal
id never crosses a tenant boundary.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import utcnow_naive
from app.db.models.session_goal import GoalAlignmentScore, SessionGoal
from app.repositories.session_goal import (
    GoalAlignmentScoreRepository,
    SessionGoalRepository,
)
from app.services import audit as audit_svc
from app.services import session as session_svc

log = logging.getLogger(__name__)


# Hardened so the slash-command path never accepts a giant paste that
# would explode the aux LLM prompt. Mirrors ``SessionGoalCreate.goal_text``.
MAX_GOAL_TEXT = 2000
MAX_CRITERIA = 20
MAX_CRITERION_LEN = 240


def _validate_goal_payload(
    *,
    goal_text: str,
    success_criteria: Sequence[str] | None,
    alignment_threshold: float | None,
) -> tuple[str, list[str], float]:
    text = (goal_text or "").strip()
    if not text:
        raise ValidationFailed(
            "goal_text required", code="session_goal.text_required"
        )
    if len(text) > MAX_GOAL_TEXT:
        raise ValidationFailed(
            "goal_text exceeds 2000 chars",
            code="session_goal.text_too_long",
            extras={"max": MAX_GOAL_TEXT},
        )
    crit_in = list(success_criteria or [])
    if len(crit_in) > MAX_CRITERIA:
        raise ValidationFailed(
            "too many success criteria",
            code="session_goal.criteria_too_many",
            extras={"max": MAX_CRITERIA},
        )
    crit_out: list[str] = []
    for entry in crit_in:
        s = str(entry or "").strip()
        if not s:
            continue
        crit_out.append(s[:MAX_CRITERION_LEN])
    threshold = 0.6 if alignment_threshold is None else float(alignment_threshold)
    if not (0.0 <= threshold <= 1.0):
        raise ValidationFailed(
            "alignment_threshold out of range",
            code="session_goal.threshold_out_of_range",
            extras={"min": 0.0, "max": 1.0},
        )
    return text, crit_out, threshold


# ─── Lock / Unlock / Patch ───────────────────────────────────
async def lock_goal(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    goal_text: str,
    success_criteria: Sequence[str] | None = None,
    alignment_threshold: float | None = None,
    metadata_json: dict[str, Any] | None = None,
    request: Request | None = None,
) -> SessionGoal:
    """Lock ``session_id`` to a fresh goal.

    Refuses to lock when an active goal already exists; the caller (UI
    or slash command) must explicitly unlock the previous one. This
    keeps the audit trail unambiguous.
    """
    sess = await session_svc.get_session_or_404(
        db, session_id, workspace_id=workspace_id
    )
    text, criteria, threshold = _validate_goal_payload(
        goal_text=goal_text,
        success_criteria=success_criteria,
        alignment_threshold=alignment_threshold,
    )

    repo = SessionGoalRepository(db)
    existing = await repo.get_active(
        session_id=sess.id, workspace_id=workspace_id
    )
    if existing is not None:
        raise Conflict(
            "another goal is already locked",
            code="session_goal.already_locked",
            extras={"goal_id": str(existing.id)},
        )

    row = await repo.create(
        workspace_id=workspace_id,
        session_id=sess.id,
        goal_text=text,
        success_criteria=criteria,
        locked_by=identity_id,
        alignment_threshold=threshold,
        metadata_json=metadata_json or {},
    )
    await audit_svc.record(
        db,
        action="goal.locked",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="session_goal",
        resource_id=row.id,
        summary=f"Locked goal for session {session_id}",
        metadata={
            "session_id": str(session_id),
            "alignment_threshold": threshold,
            "success_criteria_count": len(criteria),
        },
        request=request,
    )
    try:
        from app.services import notification_events as notif_events

        await notif_events.emit_event(
            db,
            event_key="goal.locked",
            workspace_id=workspace_id,
            actor_identity_id=identity_id,
            cooldown_resource_id=str(session_id),
            payload={
                "session_id": str(session_id),
                "goal_id": str(row.id),
                "goal_text": text[:120],
                "alignment_threshold": threshold,
            },
            request=request,
        )
    except Exception:  # pragma: no cover - notification best-effort
        log.exception("notify goal.locked failed for goal=%s", row.id)
    return row


async def unlock_goal(
    db: AsyncSession,
    *,
    goal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    request: Request | None = None,
) -> SessionGoal:
    repo = SessionGoalRepository(db)
    row = await repo.get(goal_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("goal not found", code="session_goal.not_found")
    if row.unlocked_at is not None:
        # Idempotent — return the row, no audit (avoid noisy duplicate
        # rows on a double-click).
        return row
    row.unlocked_at = utcnow_naive()
    row.unlocked_by = actor_identity_id
    await db.flush([row])
    await audit_svc.record(
        db,
        action="goal.unlocked",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="session_goal",
        resource_id=row.id,
        summary=f"Unlocked goal {goal_id}",
        metadata={"session_id": str(row.session_id)},
        request=request,
    )
    try:
        from app.services import notification_events as notif_events

        await notif_events.emit_event(
            db,
            event_key="goal.unlocked",
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            cooldown_resource_id=str(row.session_id),
            payload={
                "session_id": str(row.session_id),
                "goal_id": str(row.id),
                "goal_text": row.goal_text[:120],
            },
            request=request,
        )
    except Exception:  # pragma: no cover - notification best-effort
        log.exception("notify goal.unlocked failed for goal=%s", row.id)
    return row


async def update_goal(
    db: AsyncSession,
    *,
    goal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    goal_text: str | None = None,
    success_criteria: Sequence[str] | None = None,
    alignment_threshold: float | None = None,
    metadata_json: dict[str, Any] | None = None,
    request: Request | None = None,
) -> SessionGoal:
    repo = SessionGoalRepository(db)
    row = await repo.get(goal_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("goal not found", code="session_goal.not_found")
    if row.unlocked_at is not None:
        raise Conflict(
            "cannot edit an unlocked goal",
            code="session_goal.already_unlocked",
        )

    diff: dict[str, Any] = {}
    if goal_text is not None:
        text, _crit_unused, _t_unused = _validate_goal_payload(
            goal_text=goal_text,
            success_criteria=row.success_criteria,
            alignment_threshold=row.alignment_threshold,
        )
        if text != row.goal_text:
            diff["goal_text"] = {"from": row.goal_text, "to": text}
            row.goal_text = text
    if success_criteria is not None:
        _t_unused, criteria, _th_unused = _validate_goal_payload(
            goal_text=row.goal_text,
            success_criteria=success_criteria,
            alignment_threshold=row.alignment_threshold,
        )
        diff["success_criteria"] = {
            "from": list(row.success_criteria),
            "to": criteria,
        }
        row.success_criteria = criteria
    if alignment_threshold is not None:
        _t_unused, _crit_unused, threshold = _validate_goal_payload(
            goal_text=row.goal_text,
            success_criteria=row.success_criteria,
            alignment_threshold=alignment_threshold,
        )
        if threshold != row.alignment_threshold:
            diff["alignment_threshold"] = {
                "from": row.alignment_threshold,
                "to": threshold,
            }
            row.alignment_threshold = threshold
    if metadata_json is not None:
        diff["metadata_json"] = {"from": row.metadata_json, "to": metadata_json}
        row.metadata_json = dict(metadata_json)

    if not diff:
        return row

    await db.flush([row])
    await audit_svc.record(
        db,
        action="goal.updated",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="session_goal",
        resource_id=row.id,
        summary=f"Updated goal {goal_id}",
        metadata={"session_id": str(row.session_id), "diff": diff},
        request=request,
    )
    return row


# ─── Reads ───────────────────────────────────────────────────
async def get_goal_or_404(
    db: AsyncSession, *, goal_id: uuid.UUID, workspace_id: uuid.UUID
) -> SessionGoal:
    row = await SessionGoalRepository(db).get(goal_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("goal not found", code="session_goal.not_found")
    return row


async def get_active_goal(
    db: AsyncSession, *, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> SessionGoal | None:
    return await SessionGoalRepository(db).get_active(
        session_id=session_id, workspace_id=workspace_id
    )


async def list_goals(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    include_unlocked: bool = True,
) -> Sequence[SessionGoal]:
    return await SessionGoalRepository(db).list_for_session(
        session_id=session_id,
        workspace_id=workspace_id,
        include_unlocked=include_unlocked,
        limit=limit,
        offset=offset,
    )


async def list_alignment_scores(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int = 200,
    offset: int = 0,
) -> Sequence[GoalAlignmentScore]:
    return await GoalAlignmentScoreRepository(db).list_for_session(
        session_id=session_id,
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )


# ─── Score writer (used by the ARQ job + manual realign) ─────
async def record_score(
    db: AsyncSession,
    *,
    session_goal_id: uuid.UUID,
    message_id: uuid.UUID,
    workspace_id: uuid.UUID,
    score: float,
    rationale: str | None,
    judged_by_model: str | None,
) -> GoalAlignmentScore:
    """Insert a fresh score row.

    Multiple scores per ``(goal, message)`` are *intentional* — manual
    realign retains the history so users can see how an updated rubric
    or model rated the same turn. The latest row wins for UI rendering.
    """
    if not (0.0 <= score <= 1.0):
        raise ValidationFailed(
            "score out of range",
            code="session_goal.score_out_of_range",
        )
    goal = await get_goal_or_404(
        db, goal_id=session_goal_id, workspace_id=workspace_id
    )
    flagged = score < goal.alignment_threshold
    row = await GoalAlignmentScoreRepository(db).create(
        workspace_id=workspace_id,
        session_goal_id=goal.id,
        message_id=message_id,
        score=float(score),
        rationale=(rationale or None),
        judged_by_model=(judged_by_model or None),
        flagged=flagged,
    )
    return row
