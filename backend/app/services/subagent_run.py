"""Sub-agent run lifecycle, retry budget, and hallucination gate (M2.5.1).

This module is the single choke point for everything that mutates a
:class:`SubAgentRun` row. The capability lifecycle hooks in
:mod:`app.agents.harness.subagents` call into ``register_run`` /
``update_heartbeat`` / ``transition_state`` from the agent runner; the
60-second :func:`app.jobs.subagent_zombie.reap_zombies` cron sweeps
stale rows via ``list_stale`` + ``reap_zombie``; and the M2.5
:mod:`app.services.approval_dispatch` ``subagent_hallucination_review``
handler calls ``transition_state`` to move rows through the
HALLUCINATION_REVIEW → COMPLETED / KILLED edges once an admin decides.

Hallucination gate
------------------

When a child returns its final assistant text, the runner calls
:func:`gate_hallucination_or_approve` which:

1. Short-circuits to ``"passed"`` when the dedicated hallucination
   breaker is open (3 strikes / 5 min). Fail-open is the right tradeoff
   — a downed aux LLM must not block every child on review.
2. Runs :func:`evaluate_hallucination` for a 0..1 confidence score that
   the result is grounded in verifiable evidence (tool results, cited
   sources, etc).
3. ``score >= threshold`` (default 0.5) → marks the row COMPLETED with
   ``hallucination_score`` set; ``"passed"``.
4. ``score < threshold`` → files a pending Approval row with
   ``resource_type='subagent_hallucination_review'`` (TTL 1 day per
   the roadmap TTL strategy table) and transitions the run to
   HALLUCINATION_REVIEW. The parent waits; the M2.5 dispatch handler
   completes / kills the row when the admin decides.

Audit + breaker keys are exported as module constants so tests + the
:mod:`app.services.approval_dispatch` ``_apply_subagent_hallucination_review``
handler can reference one source of truth.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalStatus,
)
from app.db.models.subagent_run import (
    FINAL_OUTPUT_MAX_CHARS,
    SubAgentRun,
    SubAgentRunState,
)
from app.repositories.approval import ApprovalRepository
from app.repositories.subagent_run import SubAgentRunRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_HALLUCINATION_APPROVED",
    "AUDIT_HALLUCINATION_PASSED",
    "AUDIT_HALLUCINATION_REJECTED",
    "AUDIT_HALLUCINATION_REVIEW_REQUIRED",
    "AUDIT_HEARTBEAT_LOST",
    "AUDIT_RETRY_BUDGET_EXHAUSTED",
    "AUDIT_RUN_REGISTERED",
    "AUDIT_STATE_TRANSITIONED",
    "AUDIT_ZOMBIE_DETECTED",
    "AUDIT_ZOMBIE_REAPED",
    "DEFAULT_HALLUCINATION_THRESHOLD",
    "FAIL_OPEN_DEFAULT_SCORE",
    "HALLUCINATION_APPROVAL_TTL",
    "HALLUCINATION_BREAKER_BUCKET",
    "HALLUCINATION_BREAKER_RECOVER_SECONDS",
    "HALLUCINATION_BREAKER_TRIP_AT",
    "HALLUCINATION_BREAKER_WINDOW_SECONDS",
    "HALLUCINATION_RESOURCE_TYPE",
    "HEARTBEAT_DEAD_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "HallucinationVerdict",
    "RetryBudgetExhausted",
    "consume_retry_budget",
    "evaluate_hallucination",
    "gate_hallucination_or_approve",
    "list_active",
    "list_stale",
    "reap_zombie",
    "register_run",
    "transition_state",
    "update_heartbeat",
]


# ─── Tunables (single source of truth) ───────────────────────
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_DEAD_SECONDS = 300

DEFAULT_HALLUCINATION_THRESHOLD = 0.5
FAIL_OPEN_DEFAULT_SCORE = 0.5
HALLUCINATION_APPROVAL_TTL = timedelta(days=1)

HALLUCINATION_BREAKER_BUCKET = "subagent:hallucination"
HALLUCINATION_BREAKER_TRIP_AT = 3
HALLUCINATION_BREAKER_WINDOW_SECONDS = 300
HALLUCINATION_BREAKER_RECOVER_SECONDS = 1800

# Resource type written on the Approval row for hallucination review.
# Stable string string-literal — must stay in sync with the dispatch
# handler in :mod:`app.services.approval_dispatch` and the M2.5 TTL
# strategy table (1 day expiry, REJECT + cancel child on default).
HALLUCINATION_RESOURCE_TYPE = "subagent_hallucination_review"

# Sentinel tool_name to keep the legacy NOT NULL ``tool_name`` column
# happy without polluting the chat-tool-call surface (matches the M1.4
# curator pattern).
_TOOL_NAME_SENTINEL = "_subagent_hallucination_review"


# ─── Audit action keys ───────────────────────────────────────
AUDIT_RUN_REGISTERED = "subagent.run_registered"
AUDIT_STATE_TRANSITIONED = "subagent.state_transitioned"
AUDIT_HEARTBEAT_LOST = "subagent.heartbeat_lost"
AUDIT_ZOMBIE_DETECTED = "subagent.zombie_detected"
AUDIT_ZOMBIE_REAPED = "subagent.zombie_reaped"
AUDIT_RETRY_BUDGET_EXHAUSTED = "subagent.retry_budget_exhausted"
AUDIT_HALLUCINATION_PASSED = "subagent.hallucination_passed"
AUDIT_HALLUCINATION_REVIEW_REQUIRED = "subagent.hallucination_review_required"
AUDIT_HALLUCINATION_REJECTED = "subagent.hallucination_rejected"
AUDIT_HALLUCINATION_APPROVED = "subagent.hallucination_approved"


# ─── Errors ──────────────────────────────────────────────────
class RetryBudgetExhausted(AppError):
    """Raised when the parent tries to retry a child past its budget.

    Mapped to a 409 by the :class:`AppError` default; the runner
    catches this and routes the child through the permanent-failure
    path instead of looping forever.
    """

    code = "subagent.retry_budget_exhausted"
    default_status = 409


# ─── Aux LLM verdict schema ──────────────────────────────────
class HallucinationVerdict(BaseModel):
    """Structured aux LLM output for the hallucination gate.

    ``score`` mirrors the runtime threshold contract (>=0.5 trusted).
    ``rationale`` is bounded to the length the approval card preview
    can render without truncation.
    """

    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=600)
    has_evidence: bool = False


_PROMPT_SYSTEM = (
    "You are an evidence checker for a sub-agent's final output.\n"
    "Decide whether the answer is grounded in verifiable evidence:\n"
    " - Tool results, cited URLs, computed values, or quoted source text count as evidence.\n"
    " - Speculation, generic platitudes, or unattributed claims do NOT.\n"
    'Output strict JSON: {"score": float in [0,1], "rationale": short single sentence,'
    ' "has_evidence": bool}.\n'
    "score=1.0 → all major claims grounded. score=0.5 → mixed. score=0.0 → entirely ungrounded."
)


def _build_user_prompt(*, final_output: str, max_chars: int = 4000) -> str:
    """Trim ``final_output`` head + tail so the eval cost stays bounded."""
    text = (final_output or "").strip()
    if len(text) > max_chars:
        head_room = max_chars // 2
        tail_room = max_chars - head_room - 1
        text = text[:head_room] + "…" + text[-tail_room:]
    return f"FINAL OUTPUT:\n{text}"


# ─── Lifecycle ───────────────────────────────────────────────
async def register_run(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    parent_run_id: uuid.UUID,
    child_run_id: uuid.UUID,
    spawn_depth: int = 0,
    parent_session_id: uuid.UUID | None = None,
    retry_budget: int = 3,
) -> SubAgentRun:
    """Create the spine row for a freshly spawned child.

    Idempotent on ``child_run_id``: if a row already exists (lifecycle
    hook re-fired on reconnect) we return the existing row instead of
    raising on the unique-index conflict. Caller commits.
    """
    repo = SubAgentRunRepository(db)
    existing = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if existing is not None:
        return existing

    now = utcnow_naive()
    row = SubAgentRun(
        workspace_id=workspace_id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        parent_session_id=parent_session_id,
        spawn_depth=int(max(0, spawn_depth)),
        state=SubAgentRunState.RUNNING,
        last_heartbeat_at=now,
        retry_count=0,
        retry_budget=int(max(0, retry_budget)),
    )
    db.add(row)
    await db.flush([row])

    await audit_svc.record(
        db,
        action=AUDIT_RUN_REGISTERED,
        actor_identity_id=None,
        workspace_id=workspace_id,
        resource_type="subagent_run",
        resource_id=row.id,
        summary=(
            f"subagent registered: parent_run={parent_run_id} "
            f"child_run={child_run_id} depth={row.spawn_depth}"
        ),
        metadata={
            "parent_run_id": str(parent_run_id),
            "child_run_id": str(child_run_id),
            "spawn_depth": int(row.spawn_depth),
            "retry_budget": int(row.retry_budget),
        },
    )
    return row


async def update_heartbeat(
    db: AsyncSession,
    *,
    child_run_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Bump ``last_heartbeat_at`` to ``now`` for the matching row.

    Returns True when a row was updated, False when the child has no
    spine row (e.g. it was reaped between two heartbeats). Cheap
    UPDATE; never raises. Caller commits.
    """
    repo = SubAgentRunRepository(db)
    row = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if row is None:
        return False
    if row.state in _TERMINAL_STATES:
        return False
    row.last_heartbeat_at = now or utcnow_naive()
    await db.flush([row])
    return True


_TERMINAL_STATES: frozenset[SubAgentRunState] = frozenset(
    {
        SubAgentRunState.COMPLETED,
        SubAgentRunState.ZOMBIE,
        SubAgentRunState.KILLED,
        SubAgentRunState.FAILED,
    }
)


async def transition_state(
    db: AsyncSession,
    *,
    child_run_id: uuid.UUID,
    target_state: SubAgentRunState,
    reason: str | None = None,
    error_kind: str | None = None,
    final_output: str | None = None,
    hallucination_score: float | None = None,
    hallucination_approval_id: uuid.UUID | None = None,
) -> SubAgentRun:
    """Move the row through the state machine + write one audit row.

    Idempotent if the requested target equals the current state. Once
    a row reaches a terminal state (``COMPLETED`` / ``ZOMBIE`` /
    ``KILLED`` / ``FAILED``) further calls are no-ops returning the
    row unchanged — the runner reconnect path can call this safely
    without first checking the current state. Caller commits.
    """
    repo = SubAgentRunRepository(db)
    row = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if row is None:
        raise LookupError(f"no subagent_run for child_run_id={child_run_id}")

    previous = row.state
    # Same state → keep row clean, no audit spam.
    if previous == target_state:
        if final_output is not None and row.final_output is None:
            row.final_output = final_output[:FINAL_OUTPUT_MAX_CHARS]
            await db.flush([row])
        return row
    # Already terminal → no further transitions allowed.
    if previous in _TERMINAL_STATES:
        return row

    row.state = target_state
    if error_kind is not None:
        row.error_kind = error_kind[:80]
    if final_output is not None:
        row.final_output = final_output[:FINAL_OUTPUT_MAX_CHARS]
    if hallucination_score is not None:
        row.hallucination_score = float(hallucination_score)
    if hallucination_approval_id is not None:
        row.hallucination_approval_id = hallucination_approval_id
    await db.flush([row])

    await audit_svc.record(
        db,
        action=AUDIT_STATE_TRANSITIONED,
        actor_identity_id=None,
        workspace_id=row.workspace_id,
        resource_type="subagent_run",
        resource_id=row.id,
        summary=(
            f"subagent {row.child_run_id} {previous.value} → {target_state.value}"
            + (f": {reason}" if reason else "")
        ),
        metadata={
            "child_run_id": str(row.child_run_id),
            "parent_run_id": str(row.parent_run_id),
            "from_state": previous.value,
            "to_state": target_state.value,
            "reason": reason,
            "error_kind": row.error_kind,
            "spawn_depth": int(row.spawn_depth),
        },
    )
    return row


async def consume_retry_budget(
    db: AsyncSession,
    *,
    child_run_id: uuid.UUID,
) -> int:
    """Bump ``retry_count``; return the **remaining** budget.

    Raises :class:`RetryBudgetExhausted` (409) when the call would
    take ``retry_count`` past ``retry_budget`` — caller must route
    the child to the permanent-failure path. Writes a stable
    ``subagent.retry_budget_exhausted`` audit on the exhaustion edge
    so operators can spot looping children. Caller commits.
    """
    repo = SubAgentRunRepository(db)
    row = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if row is None:
        raise LookupError(f"no subagent_run for child_run_id={child_run_id}")
    remaining = max(0, row.retry_budget - row.retry_count - 1)
    if row.retry_count >= row.retry_budget:
        await audit_svc.record(
            db,
            action=AUDIT_RETRY_BUDGET_EXHAUSTED,
            actor_identity_id=None,
            workspace_id=row.workspace_id,
            resource_type="subagent_run",
            resource_id=row.id,
            summary=(
                f"subagent {row.child_run_id} retry budget exhausted "
                f"(count={row.retry_count}/{row.retry_budget})"
            ),
            metadata={
                "child_run_id": str(row.child_run_id),
                "parent_run_id": str(row.parent_run_id),
                "retry_count": int(row.retry_count),
                "retry_budget": int(row.retry_budget),
            },
        )
        raise RetryBudgetExhausted(
            f"retry budget exhausted for child_run_id={child_run_id}",
            code="subagent.retry_budget_exhausted",
            extras={
                "child_run_id": str(child_run_id),
                "retry_count": row.retry_count,
                "retry_budget": row.retry_budget,
            },
        )
    row.retry_count += 1
    await db.flush([row])
    return remaining


async def list_active(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[SubAgentRun]:
    """Active rows (RUNNING + HALLUCINATION_REVIEW), most recent first."""
    repo = SubAgentRunRepository(db)
    return list(
        await repo.list_active(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            limit=limit,
        )
    )


async def list_stale(
    db: AsyncSession,
    *,
    heartbeat_dead_seconds: int = HEARTBEAT_DEAD_SECONDS,
    now: datetime | None = None,
    limit: int = 200,
) -> list[SubAgentRun]:
    """Reaper input: ``state=RUNNING AND last_heartbeat_at < now-dead``."""
    cutoff = (now or utcnow_naive()) - timedelta(seconds=heartbeat_dead_seconds)
    repo = SubAgentRunRepository(db)
    return list(await repo.list_stale(cutoff=cutoff, limit=limit))


async def reap_zombie(
    db: AsyncSession,
    *,
    child_run_id: uuid.UUID,
    reason: str | None = None,
) -> SubAgentRun:
    """Flip a stale RUNNING row to ZOMBIE + emit M0.10 notification.

    Keeps notification emission inside the same DB session as the
    state transition so the audit row + bell fan-out land together.
    The notification fan-out itself is best-effort (M0.10 swallows
    exceptions) so a Redis blip cannot prevent zombification.
    """
    row = await transition_state(
        db,
        child_run_id=child_run_id,
        target_state=SubAgentRunState.ZOMBIE,
        reason=reason or "heartbeat dead",
        error_kind="heartbeat_lost",
    )
    await audit_svc.record(
        db,
        action=AUDIT_ZOMBIE_REAPED,
        actor_identity_id=None,
        workspace_id=row.workspace_id,
        resource_type="subagent_run",
        resource_id=row.id,
        summary=(f"subagent {row.child_run_id} reaped as zombie (parent_run={row.parent_run_id})"),
        metadata={
            "child_run_id": str(row.child_run_id),
            "parent_run_id": str(row.parent_run_id),
            "spawn_depth": int(row.spawn_depth),
            "reason": reason or "heartbeat dead",
            "last_heartbeat_at": row.last_heartbeat_at.isoformat(),
        },
    )
    await _emit_zombie_notification(db, row=row, reason=reason or "heartbeat dead")
    return row


async def _emit_zombie_notification(
    db: AsyncSession,
    *,
    row: SubAgentRun,
    reason: str,
) -> None:
    """M0.10 fan-out for ``subagent.zombie_detected``. Never raises."""
    try:
        from app.services.notification_events import emit_event

        await emit_event(
            db,
            event_key="subagent.zombie_detected",
            workspace_id=row.workspace_id,
            actor_identity_id=None,
            cooldown_resource_id=str(row.id),
            payload={
                "subagent_run_id": str(row.id),
                "parent_run_id": str(row.parent_run_id),
                "child_run_id": str(row.child_run_id),
                "spawn_depth": int(row.spawn_depth),
                "reason": reason,
                "last_heartbeat_at": row.last_heartbeat_at.isoformat(),
                "resource_type": "subagent_run",
                "resource_id": str(row.id),
            },
        )
    except Exception:  # pragma: no cover - best-effort
        log.exception("zombie notification emit failed for subagent_run %s", row.id)


# ─── Hallucination gate ──────────────────────────────────────
async def evaluate_hallucination(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    final_output: str,
    timeout_s: float = 25.0,
) -> tuple[float | None, str]:
    """Aux LLM call returning ``(score, model_label)``.

    ``score`` is ``None`` only when no aux model resolves at all (the
    workspace has no provider configured). Aux call failures inside
    the LLM client return ``(FAIL_OPEN_DEFAULT_SCORE, model_label)``
    so the gate degrades to "trust the result" instead of blocking
    every child on review during an aux outage.
    """
    from app.agents.auxiliary_client import (
        AuxiliaryTask,
        call_aux_chat,
        get_aux_model,
    )

    config = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.JUDGE)
    if config is None:
        log.info(
            "hallucination gate: no aux model for workspace %s; fail-open",
            workspace_id,
        )
        return None, "heuristic:no_aux_model"

    model_label = config.model
    user_prompt = _build_user_prompt(final_output=final_output)

    response = await call_aux_chat(
        config=config,
        system=_PROMPT_SYSTEM,
        user=user_prompt,
        response_format=HallucinationVerdict,
        timeout_s=timeout_s,
    )
    if isinstance(response, HallucinationVerdict):
        return float(response.score), model_label

    log.info(
        "hallucination gate: aux returned unparseable shape for workspace %s",
        workspace_id,
    )
    return FAIL_OPEN_DEFAULT_SCORE, model_label


async def gate_hallucination_or_approve(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    child_run: SubAgentRun,
    final_output: str,
    threshold: float = DEFAULT_HALLUCINATION_THRESHOLD,
    timeout_s: float = 25.0,
) -> Literal["passed", "approval_required"]:
    """Run the hallucination gate for one child's final output.

    Behaviour matches the M2.5.1 design:

    * Breaker open → record gate skipped + transition COMPLETED
      (fail-open). The breaker bumps when the aux call itself
      raises; classification failures (parse / timeout) do **not**
      bump because they're already part of the fail-open contract.
    * ``score >= threshold`` → COMPLETED with score persisted. Audit
      ``subagent.hallucination_passed``.
    * ``score < threshold`` → file Approval row, transition
      HALLUCINATION_REVIEW. Audit
      ``subagent.hallucination_review_required``.

    Caller commits.
    """
    # Breaker check first — fail-open before we burn another aux call.
    from app.jobs._breaker import (
        bump_failure,
        is_breaker_open,
    )

    workspace_str = str(workspace_id)
    if await is_breaker_open(
        bucket=HALLUCINATION_BREAKER_BUCKET,
        workspace_id=workspace_str,
        trip_at=HALLUCINATION_BREAKER_TRIP_AT,
    ):
        await transition_state(
            db,
            child_run_id=child_run.child_run_id,
            target_state=SubAgentRunState.COMPLETED,
            reason="hallucination breaker open — fail-open",
            final_output=final_output,
        )
        await audit_svc.record(
            db,
            action=AUDIT_HALLUCINATION_PASSED,
            actor_identity_id=None,
            workspace_id=workspace_id,
            resource_type="subagent_run",
            resource_id=child_run.id,
            summary=(f"subagent {child_run.child_run_id} passed gate (breaker open)"),
            metadata={
                "child_run_id": str(child_run.child_run_id),
                "parent_run_id": str(child_run.parent_run_id),
                "score": None,
                "model": "breaker_open",
                "reason": "breaker_open",
            },
        )
        return "passed"

    # Run the aux LLM evaluation. Any internal failure trips the
    # breaker so the next child gets the cheap fail-open path.
    try:
        score, model_label = await evaluate_hallucination(
            db,
            workspace_id=workspace_id,
            final_output=final_output,
            timeout_s=timeout_s,
        )
    except Exception:
        log.exception("hallucination eval raised for workspace %s", workspace_id)
        await bump_failure(
            bucket=HALLUCINATION_BREAKER_BUCKET,
            workspace_id=workspace_str,
            window_seconds=HALLUCINATION_BREAKER_WINDOW_SECONDS,
            recover_seconds=HALLUCINATION_BREAKER_RECOVER_SECONDS,
        )
        score, model_label = FAIL_OPEN_DEFAULT_SCORE, "heuristic:eval_raised"

    # No aux configured at all → mirror the breaker fail-open path.
    if score is None:
        await transition_state(
            db,
            child_run_id=child_run.child_run_id,
            target_state=SubAgentRunState.COMPLETED,
            reason="no aux model configured — fail-open",
            final_output=final_output,
        )
        await audit_svc.record(
            db,
            action=AUDIT_HALLUCINATION_PASSED,
            actor_identity_id=None,
            workspace_id=workspace_id,
            resource_type="subagent_run",
            resource_id=child_run.id,
            summary=(f"subagent {child_run.child_run_id} passed gate (no aux model)"),
            metadata={
                "child_run_id": str(child_run.child_run_id),
                "parent_run_id": str(child_run.parent_run_id),
                "score": None,
                "model": model_label,
                "reason": "no_aux_model",
            },
        )
        return "passed"

    score_value = float(score)

    if score_value >= threshold:
        await transition_state(
            db,
            child_run_id=child_run.child_run_id,
            target_state=SubAgentRunState.COMPLETED,
            reason=f"hallucination score {score_value:.2f} >= {threshold:.2f}",
            final_output=final_output,
            hallucination_score=score_value,
        )
        await audit_svc.record(
            db,
            action=AUDIT_HALLUCINATION_PASSED,
            actor_identity_id=None,
            workspace_id=workspace_id,
            resource_type="subagent_run",
            resource_id=child_run.id,
            summary=(f"subagent {child_run.child_run_id} passed gate score={score_value:.2f}"),
            metadata={
                "child_run_id": str(child_run.child_run_id),
                "parent_run_id": str(child_run.parent_run_id),
                "score": score_value,
                "threshold": float(threshold),
                "model": model_label,
            },
        )
        return "passed"

    # Below threshold → file an Approval and park the run.
    approval = await _file_hallucination_approval(
        db,
        workspace_id=workspace_id,
        child_run=child_run,
        final_output=final_output,
        score=score_value,
        threshold=threshold,
        model_label=model_label,
    )
    await transition_state(
        db,
        child_run_id=child_run.child_run_id,
        target_state=SubAgentRunState.HALLUCINATION_REVIEW,
        reason=f"hallucination score {score_value:.2f} < {threshold:.2f}",
        final_output=final_output,
        hallucination_score=score_value,
        hallucination_approval_id=approval.id,
    )
    await audit_svc.record(
        db,
        action=AUDIT_HALLUCINATION_REVIEW_REQUIRED,
        actor_identity_id=None,
        workspace_id=workspace_id,
        resource_type="subagent_run",
        resource_id=child_run.id,
        summary=(
            f"subagent {child_run.child_run_id} hallucination review required "
            f"(score={score_value:.2f})"
        ),
        metadata={
            "child_run_id": str(child_run.child_run_id),
            "parent_run_id": str(child_run.parent_run_id),
            "approval_id": str(approval.id),
            "score": score_value,
            "threshold": float(threshold),
            "model": model_label,
        },
    )
    return "approval_required"


async def _file_hallucination_approval(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    child_run: SubAgentRun,
    final_output: str,
    score: float,
    threshold: float,
    model_label: str,
) -> Approval:
    """Persist a pending Approval row for the M2.5 dispatch handler."""
    repo = ApprovalRepository(db)
    summary_preview = (final_output or "").strip().splitlines()[0:1]
    summary = f"sub-agent hallucination review (score {score:.2f} < {threshold:.2f})" + (
        f": {summary_preview[0][:120]}" if summary_preview else ""
    )
    body: dict[str, Any] = {
        "subagent_run_id": str(child_run.id),
        "child_run_id": str(child_run.child_run_id),
        "parent_run_id": str(child_run.parent_run_id),
        "spawn_depth": int(child_run.spawn_depth),
        "score": float(score),
        "threshold": float(threshold),
        "model": model_label,
        "final_output_excerpt": (final_output or "")[:FINAL_OUTPUT_MAX_CHARS],
    }
    return await repo.create(
        workspace_id=workspace_id,
        session_id=child_run.parent_session_id,
        agent_id=None,
        run_id=child_run.parent_run_id,
        tool_name=_TOOL_NAME_SENTINEL,
        tool_args=body,
        summary=summary,
        requested_by_identity_id=None,
        expires_at=utcnow_naive() + HALLUCINATION_APPROVAL_TTL,
        resource_type=HALLUCINATION_RESOURCE_TYPE,
        resource_id=child_run.id,
    )


# ─── Approval dispatch hooks (called from approval_dispatch) ─
async def apply_hallucination_decision(
    db: AsyncSession,
    *,
    approval: Approval,
    approved: bool,
    actor_identity_id: uuid.UUID | None,
) -> SubAgentRun | None:
    """Drive the post-decision side effect for a hallucination review.

    Looks up the parked ``SubAgentRun`` by ``resource_id`` (preferred)
    or by ``child_run_id`` from the body (fallback). Approve →
    COMPLETED + ``subagent.hallucination_approved`` audit. Reject →
    KILLED + ``subagent.hallucination_rejected`` audit + the parent
    is expected to receive the cancel via the next heartbeat tick.

    Returns the updated row, or ``None`` when the spine row is gone
    (idempotent — admin clicked decide on a row whose run already
    completed/zombified between propose and decide).
    """
    repo = SubAgentRunRepository(db)
    spine_id: uuid.UUID | None = approval.resource_id
    row: SubAgentRun | None = None
    if spine_id is not None:
        row = await repo.get(spine_id)
    if row is None:
        # Fallback: body carries child_run_id.
        body = approval.tool_args or {}
        child_raw = body.get("child_run_id")
        if child_raw:
            try:
                child_run_id = uuid.UUID(str(child_raw))
            except (TypeError, ValueError):
                return None
            row = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if row is None:
        return None

    if approved:
        target = SubAgentRunState.COMPLETED
        audit_action = AUDIT_HALLUCINATION_APPROVED
        decision = "approved"
        new_error_kind = None
    else:
        target = SubAgentRunState.KILLED
        audit_action = AUDIT_HALLUCINATION_REJECTED
        decision = "rejected"
        new_error_kind = "hallucination_rejected"

    updated = await transition_state(
        db,
        child_run_id=row.child_run_id,
        target_state=target,
        reason=f"hallucination review {decision}",
        error_kind=new_error_kind,
    )
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=row.workspace_id,
        resource_type="subagent_run",
        resource_id=row.id,
        summary=(
            f"subagent {row.child_run_id} hallucination review {decision} (approval={approval.id})"
        ),
        metadata={
            "approval_id": str(approval.id),
            "child_run_id": str(row.child_run_id),
            "parent_run_id": str(row.parent_run_id),
            "spawn_depth": int(row.spawn_depth),
            "score": float(row.hallucination_score)
            if row.hallucination_score is not None
            else None,
        },
    )
    return updated


# ─── Convenience: hallucination decision via raw approval id ─
async def cancel_pending_hallucination_for_child(
    db: AsyncSession,
    *,
    child_run_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str = "child run cancelled",
) -> Approval | None:
    """Cancel the dangling Approval when the child terminates first.

    Race window: the parent cancels the child while the hallucination
    Approval is still pending. We flip the Approval to CANCELLED so
    admin doesn't see a phantom row, and leave the SubAgentRun's
    state alone (caller already moved it to KILLED / FAILED).
    """
    repo = SubAgentRunRepository(db)
    row = await repo.get_by_child_run_id(child_run_id=child_run_id)
    if row is None or row.hallucination_approval_id is None:
        return None
    approval_repo = ApprovalRepository(db)
    decided = await approval_repo.decide(
        approval_id=row.hallucination_approval_id,
        workspace_id=row.workspace_id,
        approved=False,
        reason=reason,
        decided_by_identity_id=actor_identity_id,
        now=utcnow_naive(),
        status_override=ApprovalStatus.CANCELLED,
    )
    return decided
