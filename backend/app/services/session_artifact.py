"""Per-run artifact capture service (M0.2).

A ``SessionArtifact`` is the immutable, structured record of one agent
run — what the user asked, what the model thought / called / answered,
how many iterations it took and how it ended. It is the input shape
PRM (M0.3), Curator (M2.x) and Evolver (M3.x) consume.

Capture is **idempotent** on ``(workspace_id, run_id)``: a second call
for the same run is a no-op that returns the row already on disk. The
capture path also runs **fail-open** — any exception is swallowed and
written to ``audit_events(action="artifact.capture_failed")`` so the
chat round-trip never breaks because the artifact pipeline is down.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.models.session_goal import GoalAlignmentScore
from app.repositories.session_artifact import SessionArtifactRepository
from app.schemas.session_artifact import ArtifactOutcome, ArtifactTurn, TurnRole
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


# Recognised RunEvent kinds the fold function understands. Any other
# kind is dropped silently — the WS layer may add new frame types
# (heartbeat, resume_ack, etc.) that have no place in the artifact.
_KIND_DELTA = "delta"
_KIND_THINKING = "thinking"
_KIND_TOOL_CALL = "tool_call"
_KIND_TOOL_RESULT = "tool_result"
_KIND_FINAL = "final"
_KIND_ERROR = "error"
_KIND_ITERATION = "iteration_marker"
_KIND_GRAPH_NODE = "graph_node_tick"


# ─── Pure helpers (module-level so unit tests can hit them) ─────────
def _hash_user_text(text: str) -> str:
    """SHA-256 of NFC-normalised, stripped user text.

    Normalisation guarantees that "café" typed two different ways still
    dedups to one hash — relevant once M3 starts cross-workspace
    federation and we hash at the boundary.
    """
    norm = unicodedata.normalize("NFC", (text or "").strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _new_assistant_turn(iteration: int) -> ArtifactTurn:
    return ArtifactTurn(role=TurnRole.ASSISTANT, text="", iteration=iteration)


def _fold_events_to_turns(
    events: Sequence[dict[str, Any]], user_text: str
) -> tuple[list[ArtifactTurn], list[str], int]:
    """Fold raw RunEvent dicts into a list of :class:`ArtifactTurn`.

    ``events`` items are expected to be ``{"kind": <RunEventKind value>,
    "data": {...}}`` dicts in arrival order. The function never raises
    — unknown frame kinds are skipped so a forward-compatible event
    stream can't break artifact capture for older workers.

    Returns ``(turns, invoked_tools_sorted, iteration_count)`` where:

    * ``turns`` always starts with the synthetic user turn at
      ``iteration=0``.
    * ``iteration_count`` is the number of distinct assistant
      iterations recorded (each tool_call → tool_result cycle followed
      by fresh model output bumps the counter).
    * ``invoked_tools`` is the sorted-unique list of tool names seen.
    """
    turns: list[ArtifactTurn] = [
        ArtifactTurn(
            role=TurnRole.USER,
            text=user_text or "",
            iteration=0,
        )
    ]
    invoked: set[str] = set()
    iteration = 1
    current: ArtifactTurn | None = None
    saw_tool_result = False

    def _ensure_assistant() -> ArtifactTurn:
        nonlocal current, saw_tool_result, iteration
        if saw_tool_result and current is not None:
            iteration += 1
            current = None
            saw_tool_result = False
        if current is None:
            current = _new_assistant_turn(iteration)
            turns.append(current)
        return current

    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}

        if kind == _KIND_DELTA:
            turn = _ensure_assistant()
            chunk = data.get("text") or ""
            if chunk:
                turn.text = (turn.text or "") + str(chunk)
        elif kind == _KIND_THINKING:
            turn = _ensure_assistant()
            chunk = data.get("text") or ""
            if chunk:
                turn.thinking = (turn.thinking or "") + str(chunk)
        elif kind == _KIND_TOOL_CALL:
            turn = _ensure_assistant()
            name = str(data.get("name") or "")
            if name:
                invoked.add(name)
            turn.tool_calls.append(
                {
                    "name": name,
                    "args": data.get("args") or {},
                    "call_id": data.get("id") or data.get("call_id"),
                }
            )
        elif kind == _KIND_TOOL_RESULT:
            turn = _ensure_assistant()
            err = data.get("error")
            turn.tool_results.append(
                {
                    "call_id": data.get("id") or data.get("call_id"),
                    "ok": err is None,
                    "data": data.get("result"),
                    "error": err,
                }
            )
            saw_tool_result = True
        elif kind == _KIND_FINAL:
            turn = _ensure_assistant()
            text_payload = data.get("text")
            if text_payload and not turn.text:
                turn.text = str(text_payload)
            mid = _coerce_uuid(data.get("message_id"))
            if mid is not None:
                turn.message_id = mid
        elif kind in (_KIND_ITERATION, _KIND_GRAPH_NODE):
            # Explicit iteration markers (rare today, but the runner
            # may start emitting them once we instrument graph nodes
            # for tracing). Treat as a forced bump.
            if current is not None:
                iteration += 1
                current = None
                saw_tool_result = False
        elif kind == _KIND_ERROR:
            turn = _ensure_assistant()
            note = data.get("message") or data.get("code") or ""
            if note:
                turn.text = (turn.text or "") + f"\n[error: {note}]"
        # Other kinds (USAGE / APPROVAL_UPDATE / heartbeat / unknown)
        # are intentionally dropped.

    has_assistant = any(t.role == TurnRole.ASSISTANT for t in turns)
    iteration_count = iteration if has_assistant else 0
    return turns, sorted(invoked), iteration_count


def _infer_final_outcome(
    events: Sequence[dict[str, Any]],
    raised_exc: BaseException | None,
) -> tuple[str, str | None]:
    """Heuristic ``(outcome, error_kind)`` from a captured event stream.

    * Cancellation (``asyncio.CancelledError`` from the WS turn task)
      is the only signal that maps to ``cancelled``.
    * A ``RunEventKind.FINAL`` with no error → ``success``.
    * An ``ERROR`` frame **and** any assistant text/tool_call already
      emitted → ``partial`` (the user got *something*).
    * Pure failure with no assistant output → ``error``.
    """
    if isinstance(raised_exc, BaseException):
        # ``asyncio.CancelledError`` is a BaseException in 3.12; check by
        # name to avoid importing asyncio just for type narrowing here.
        cls_name = type(raised_exc).__name__
        if cls_name == "CancelledError":
            return ArtifactOutcome.CANCELLED.value, None

    saw_final = False
    saw_error = False
    saw_assistant_output = False
    error_kind: str | None = None
    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if kind == _KIND_FINAL:
            saw_final = True
        elif kind == _KIND_ERROR:
            saw_error = True
            if not error_kind:
                code = data.get("code")
                if code:
                    error_kind = str(code)
                elif raised_exc is not None:
                    error_kind = type(raised_exc).__name__
                else:
                    error_kind = "unknown"
        elif kind in (_KIND_DELTA, _KIND_TOOL_CALL):
            saw_assistant_output = True

    if raised_exc is not None and not saw_final:
        return (
            ArtifactOutcome.PARTIAL.value if saw_assistant_output else ArtifactOutcome.ERROR.value,
            error_kind or type(raised_exc).__name__,
        )

    if saw_error and saw_assistant_output:
        return ArtifactOutcome.PARTIAL.value, error_kind
    if saw_error and not saw_final:
        return ArtifactOutcome.ERROR.value, error_kind
    if saw_final:
        return ArtifactOutcome.SUCCESS.value, None
    if saw_assistant_output:
        return ArtifactOutcome.PARTIAL.value, error_kind
    return ArtifactOutcome.ERROR.value, error_kind or "no_final_event"


def _serialise_turns(turns: Sequence[ArtifactTurn]) -> list[dict]:
    """JSON-safe form of ``turns`` for JSONB persistence."""
    return [t.model_dump(mode="json") for t in turns]


# ─── DB-backed orchestration ─────────────────────────────────────────
async def capture_artifact(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    user_text: str,
    events: Sequence[dict[str, Any]],
    final_outcome: str,
    error_kind: str | None = None,
    injected_skill_pack_ids: Sequence[uuid.UUID] | Sequence[str] | None = None,
    finished_at: datetime | None = None,
) -> SessionArtifact:
    """Idempotent capture. Re-entry on the same run returns the existing row."""
    repo = SessionArtifactRepository(db)
    existing = await repo.get_by_run_id(workspace_id=workspace_id, run_id=run_id)
    if existing is not None:
        return existing

    turns, invoked_tools, iteration_count = _fold_events_to_turns(events, user_text)
    user_text_hash = _hash_user_text(user_text)
    finished = finished_at if finished_at is not None else utcnow_naive()
    if finished.tzinfo is not None:
        # Persist as naive UTC — matches the project convention noted in
        # ``utcnow_naive``.
        finished = finished.astimezone(tz=None).replace(tzinfo=None)

    pack_ids: list[str] = []
    for raw in injected_skill_pack_ids or ():
        pack_ids.append(str(raw))

    goal_alignment_avg = await _compute_goal_alignment_avg(
        db,
        workspace_id=workspace_id,
        message_ids=[t.message_id for t in turns if t.message_id is not None],
    )

    try:
        row = await repo.create(
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
            user_text_hash=user_text_hash,
            turns_json=_serialise_turns(turns),
            injected_skill_pack_ids=pack_ids,
            invoked_tools=invoked_tools,
            iteration_count=int(iteration_count),
            final_outcome=str(final_outcome),
            error_kind=error_kind,
            goal_alignment_avg=goal_alignment_avg,
            finished_at=finished,
        )
    except IntegrityError:
        # Lost the unique-index race against a concurrent capture (e.g.
        # WS reconnect retried the FINAL handler). Roll back and serve
        # the winner instead — preserves true idempotency.
        await db.rollback()
        existing = await repo.get_by_run_id(
            workspace_id=workspace_id, run_id=run_id
        )
        if existing is not None:
            return existing
        raise

    await audit_svc.record(
        db,
        action="artifact.captured",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="session_artifact",
        resource_id=row.id,
        summary=f"captured artifact for run {run_id}",
        metadata={
            "run_id": str(run_id),
            "session_id": str(session_id),
            "iteration_count": int(iteration_count),
            "final_outcome": str(final_outcome),
            "tool_count": len(invoked_tools),
        },
    )
    return row


async def capture_from_run_outcome(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    user_text: str,
    events: Sequence[dict[str, Any]],
    raised_exc: BaseException | None = None,
    final_outcome: str | None = None,
    error_kind: str | None = None,
    injected_skill_pack_ids: Sequence[uuid.UUID] | Sequence[str] | None = None,
    finished_at: datetime | None = None,
) -> SessionArtifact | None:
    """Shared entry point used by both WS and channel/flow paths.

    Wraps :func:`capture_artifact` in an outer try/except so a downed
    artifact pipeline cannot break the user-facing run lifecycle. On
    failure, writes ``audit_events(action="artifact.capture_failed")``
    and returns ``None``.
    """
    if final_outcome is None:
        final_outcome, inferred_kind = _infer_final_outcome(events, raised_exc)
        if error_kind is None:
            error_kind = inferred_kind

    try:
        row = await capture_artifact(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
            user_text=user_text,
            events=events,
            final_outcome=final_outcome,
            error_kind=error_kind,
            injected_skill_pack_ids=injected_skill_pack_ids,
            finished_at=finished_at,
        )
        return row
    except Exception as exc:
        log.exception("session artifact capture failed for run %s", run_id)
        try:
            await audit_svc.record(
                db,
                action="artifact.capture_failed",
                actor_identity_id=identity_id,
                workspace_id=workspace_id,
                resource_type="session_artifact",
                resource_id=None,
                summary=f"artifact capture failed for run {run_id}",
                metadata={
                    "run_id": str(run_id),
                    "session_id": str(session_id),
                    "error_class": exc.__class__.__name__,
                },
            )
        except Exception:  # pragma: no cover
            log.exception("audit write for capture failure also failed")
        return None


async def get_artifact(
    db: AsyncSession, *, workspace_id: uuid.UUID, run_id: uuid.UUID
) -> SessionArtifact | None:
    return await SessionArtifactRepository(db).get_by_run_id(
        workspace_id=workspace_id, run_id=run_id
    )


async def get_artifact_by_id(
    db: AsyncSession, *, workspace_id: uuid.UUID, artifact_id: uuid.UUID
) -> SessionArtifact:
    row = await SessionArtifactRepository(db).get(artifact_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        raise NotFound("artifact not found", code="session_artifact.not_found")
    return row


async def list_artifacts_for_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[SessionArtifact]:
    return await SessionArtifactRepository(db).list_by_session(
        workspace_id=workspace_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


async def list_recent_for_workspace(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    since: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> Sequence[SessionArtifact]:
    return await SessionArtifactRepository(db).list_recent_for_workspace(
        workspace_id=workspace_id,
        since=since,
        limit=limit,
        offset=offset,
    )


async def update_judge_score(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    judge_score: float,
) -> SessionArtifact:
    """M0.3 hook — judge writes back its score on the captured artifact."""
    if not (-1.0 <= judge_score <= 1.0):
        raise ValueError("judge_score must be in [-1, 1]")
    row = await get_artifact_by_id(
        db, workspace_id=workspace_id, artifact_id=artifact_id
    )
    row.judge_score = float(judge_score)
    await db.flush([row])
    await db.refresh(row)
    return row


async def _compute_goal_alignment_avg(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    message_ids: Sequence[uuid.UUID | None],
) -> float | None:
    """Average of the latest GoalAlignmentScore per message in this run."""
    valid_ids = [mid for mid in message_ids if mid is not None]
    if not valid_ids:
        return None
    stmt = (
        select(GoalAlignmentScore.score)
        .where(
            GoalAlignmentScore.workspace_id == workspace_id,
            GoalAlignmentScore.message_id.in_(valid_ids),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None
    return float(sum(rows)) / float(len(rows))
