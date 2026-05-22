"""Batch-replay service — session checkpoints + reruns + diff reporting.

Three responsibilities:

1. ``capture_checkpoint`` — snapshot the current message cursor of a session
   so fork/rewind has a reference point.
2. ``fork_session`` — spin up a clone of a session up to a checkpoint's
   ``message_count`` so a user can branch without mutating history.
3. ``execute_batch`` — runs every BatchRunCase sequentially against a
   candidate Agent, diffs the output against the baseline, and writes
   per-case + aggregate stats back to the DB.

We reuse :mod:`app.services.agent_runner` for actual model dispatch so the
same Kernel + capabilities/policies/sandbox layering applies.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.security import utcnow_naive
from app.db.models.batch import (
    BatchCaseStatus,
    BatchRun,
    BatchRunStatus,
)
from app.db.models.checkpoint import SessionCheckpoint
from app.db.models.message import MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.agent import AgentRepository
from app.repositories.batch import (
    BatchRunCaseRepository,
    BatchRunRepository,
    SessionCheckpointRepository,
)
from app.repositories.session import MessageRepository, SessionRepository
from app.services import agent_runner as runner
from app.services import session as sess_svc

log = logging.getLogger(__name__)


# ─── Checkpoint CRUD ─────────────────────────────────────
async def capture_checkpoint(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_obj: SessionModel,
    label: str,
    description: str | None,
    created_by: uuid.UUID | None,
) -> SessionCheckpoint:
    count = await MessageRepository(db).count(session_id=session_obj.id)
    snapshot = {
        "title": session_obj.title,
        "kind": (
            session_obj.kind.value if hasattr(session_obj.kind, "value") else str(session_obj.kind)
        ),
        "subject_id": str(session_obj.subject_id) if session_obj.subject_id else None,
        "channel_id": str(session_obj.channel_id) if session_obj.channel_id else None,
        "metadata_json": dict(session_obj.metadata_json or {}),
    }
    return await SessionCheckpointRepository(db).create(
        workspace_id=workspace_id,
        session_id=session_obj.id,
        label=label,
        description=description,
        message_count=count,
        snapshot_json=snapshot,
        created_by=created_by,
    )


# ─── Fork / rewind ───────────────────────────────────────
async def fork_at_checkpoint(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
    created_by: uuid.UUID | None,
    title_override: str | None,
) -> tuple[SessionModel, SessionModel, int]:
    """Copy the source session's messages up to ``message_count`` into a
    fresh session. Returns ``(original, fork, copied_count)``."""

    checkpoint = await SessionCheckpointRepository(db).get(checkpoint_id)
    if checkpoint is None or checkpoint.workspace_id != workspace_id:
        raise NotFound("checkpoint_not_found", code="checkpoint.not_found")

    original = await sess_svc.get_session_or_404(
        db, checkpoint.session_id, workspace_id=workspace_id
    )
    # Pull the first ``message_count`` messages in chronological order.
    messages = await MessageRepository(db).list_for_session(
        session_id=original.id,
        limit=max(1, checkpoint.message_count),
        offset=0,
    )
    messages = list(messages)[: checkpoint.message_count]

    fork = await SessionRepository(db).create(
        workspace_id=workspace_id,
        owner_identity_id=created_by,
        kind=original.kind,
        subject_id=original.subject_id,
        channel_id=original.channel_id,
        title=title_override or f"{original.title or 'session'} (fork)",
        metadata_json={
            **(original.metadata_json or {}),
            "forked_from": {
                "session_id": str(original.id),
                "checkpoint_id": str(checkpoint.id),
                "message_count": checkpoint.message_count,
            },
        },
    )
    for m in messages:
        await sess_svc.append_message(
            db,
            session_obj=fork,
            role=m.role,
            content_json=m.content_json,
            author_identity_id=m.author_identity_id,
            author_agent_id=m.author_agent_id,
            attachments_json=list(m.attachments_json or []),
            tool_call_json=m.tool_call_json,
            tool_result_json=m.tool_result_json,
            thinking_json=m.thinking_json,
            token_usage_json=dict(m.token_usage_json or {}),
        )
    return original, fork, len(messages)


# ─── Batch replay ────────────────────────────────────────
async def create_batch_run(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    description: str | None,
    agent_id: uuid.UUID,
    cases_raw: list[dict[str, Any]],
    config_json: dict[str, Any],
) -> BatchRun:
    """Persist the run + one row per case. Does NOT kick off execution —
    the caller owns scheduling (either inline or via arq)."""

    agent = await AgentRepository(db).get(agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise NotFound("agent_not_found", code="agent.not_found")

    batch = await BatchRunRepository(db).create(
        workspace_id=workspace_id,
        name=name,
        description=description,
        agent_id=agent_id,
        status=BatchRunStatus.PENDING,
        config_json=config_json,
        stats_json={"total": len(cases_raw)},
        created_by=created_by,
    )

    for raw in cases_raw:
        text, source_sid, checkpoint_id, baseline = await _materialize_case(
            db, workspace_id=workspace_id, raw=raw
        )
        await BatchRunCaseRepository(db).create(
            workspace_id=workspace_id,
            batch_run_id=batch.id,
            case_label=raw.get("label"),
            input_text=text,
            source_session_id=source_sid,
            checkpoint_id=checkpoint_id,
            status=BatchCaseStatus.PENDING,
            baseline_text=baseline,
        )
    return batch


async def _materialize_case(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    raw: dict[str, Any],
) -> tuple[str, uuid.UUID | None, uuid.UUID | None, str | None]:
    """Translate a ``BatchCaseIn``-shaped dict into (input_text, source_sid,
    checkpoint_id, baseline_text) for persistence.

    - Text-only case: no baseline.
    - Session / checkpoint case: input = first user message; baseline =
      last assistant message at that cursor.
    """

    text = raw.get("text")
    source_sid = _parse_uuid(raw.get("source_session_id"))
    checkpoint_id = _parse_uuid(raw.get("checkpoint_id"))

    if text:
        return str(text), source_sid, checkpoint_id, None

    if checkpoint_id is not None:
        checkpoint = await SessionCheckpointRepository(db).get(checkpoint_id)
        if checkpoint is None or checkpoint.workspace_id != workspace_id:
            raise NotFound("checkpoint_not_found", code="checkpoint.not_found")
        source_sid = checkpoint.session_id
        limit = max(1, checkpoint.message_count)
    elif source_sid is not None:
        sess = await SessionRepository(db).get(source_sid)
        if sess is None or sess.workspace_id != workspace_id:
            raise NotFound("session_not_found", code="session.not_found")
        limit = 200
    else:
        raise ValidationFailed(
            "case_requires_input",
            code="batch.case_missing_input",
            extras={"needed": "text | source_session_id | checkpoint_id"},
        )

    messages = await MessageRepository(db).list_for_session(session_id=source_sid, limit=limit)
    first_user = next((m for m in messages if m.role == MessageRole.USER), None)
    last_assistant = next(
        (m for m in reversed(list(messages)) if m.role == MessageRole.ASSISTANT),
        None,
    )
    if first_user is None:
        raise ValidationFailed(
            "case_no_user_message",
            code="batch.case_no_user_message",
            extras={"source_session_id": str(source_sid)},
        )
    input_text = str((first_user.content_json or {}).get("text") or "")
    baseline = (
        str((last_assistant.content_json or {}).get("text") or "")
        if last_assistant is not None
        else None
    )
    return input_text, source_sid, checkpoint_id, baseline


async def execute_batch(batch_run_id: uuid.UUID) -> None:
    """Drive a batch end-to-end.

    Opens its own DB session per case so a long batch doesn't pin one
    connection for minutes on end. Errors on a single case are captured in
    ``BatchRunCase.error`` without aborting the whole run.
    """

    factory = get_session_factory()
    async with factory() as db:
        batch = await BatchRunRepository(db).get(batch_run_id)
        if batch is None:
            log.warning("execute_batch: batch_run %s not found", batch_run_id)
            return
        if batch.status not in {BatchRunStatus.PENDING, BatchRunStatus.RUNNING}:
            return
        batch = await BatchRunRepository(db).update(
            batch,
            status=BatchRunStatus.RUNNING,
            started_at=utcnow_naive(),
        )
        workspace_id = batch.workspace_id
        agent_id = batch.agent_id
        await db.commit()

    cases = []
    async with factory() as db:
        cases = list(await BatchRunCaseRepository(db).list_for_run(batch_run_id=batch_run_id))

    total = len(cases)
    passed = 0
    failed = 0
    skipped = 0
    start = datetime.utcnow()

    for case in cases:
        await _execute_case(
            batch_run_id=batch_run_id,
            case_id=case.id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )

    # Aggregate.
    async with factory() as db:
        rerun_cases = await BatchRunCaseRepository(db).list_for_run(batch_run_id=batch_run_id)
        for c in rerun_cases:
            if c.status == BatchCaseStatus.SUCCEEDED:
                passed += 1
            elif c.status == BatchCaseStatus.FAILED:
                failed += 1
            elif c.status == BatchCaseStatus.SKIPPED:
                skipped += 1
        stats = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_ms": int((datetime.utcnow() - start).total_seconds() * 1000),
        }
        final_status = BatchRunStatus.SUCCEEDED if failed == 0 else BatchRunStatus.FAILED
        batch = await BatchRunRepository(db).get(batch_run_id)
        if batch is not None:
            await BatchRunRepository(db).update(
                batch,
                status=final_status,
                finished_at=utcnow_naive(),
                stats_json=stats,
            )
            await db.commit()


async def _execute_case(
    *,
    batch_run_id: uuid.UUID,
    case_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> None:
    """Run one case. Creates a fresh replay_session for observability; the
    session is a plain P2P with ``metadata.origin=batch`` so audit / usage
    rollups filter cleanly."""

    factory = get_session_factory()
    async with factory() as db:
        case = await BatchRunCaseRepository(db).get(case_id)
        if case is None:
            return
        await BatchRunCaseRepository(db).update(case, status=BatchCaseStatus.RUNNING)
        if agent_id is None:
            await BatchRunCaseRepository(db).update(
                case,
                status=BatchCaseStatus.SKIPPED,
                error="batch.agent_missing",
            )
            await db.commit()
            return

        replay_session = await SessionRepository(db).create(
            workspace_id=workspace_id,
            owner_identity_id=None,
            kind=SessionKind.P2P,
            subject_id=agent_id,
            title=f"batch:{batch_run_id} case:{case_id}"[:128],
            metadata_json={
                "origin": "batch",
                "batch_run_id": str(batch_run_id),
                "batch_run_case_id": str(case_id),
            },
        )
        replay_session_id = replay_session.id
        input_text = case.input_text
        baseline_text = case.baseline_text or ""
        await db.commit()

    started = datetime.utcnow()
    try:
        async with factory() as db:
            result = await runner.run_agent_one_shot(
                db,
                workspace_id=workspace_id,
                agent_id=agent_id,
                session_id=replay_session_id,
                identity_id=None,
                user_text=input_text,
                iteration_budget=8,
            )
            await db.commit()
    except Exception as e:  # pragma: no cover
        log.exception("batch case %s failed", case_id)
        async with factory() as db:
            case = await BatchRunCaseRepository(db).get(case_id)
            if case is not None:
                await BatchRunCaseRepository(db).update(
                    case,
                    status=BatchCaseStatus.FAILED,
                    error=str(e),
                    replay_session_id=replay_session_id,
                    duration_ms=int((datetime.utcnow() - started).total_seconds() * 1000),
                )
                await db.commit()
        return

    duration_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
    output_text = result.final_text or ""
    diff_payload = _diff_texts(baseline_text, output_text) if baseline_text else {}

    async with factory() as db:
        case = await BatchRunCaseRepository(db).get(case_id)
        if case is None:
            return
        if result.error:
            await BatchRunCaseRepository(db).update(
                case,
                status=BatchCaseStatus.FAILED,
                output_text=output_text,
                error=result.error,
                duration_ms=duration_ms,
                replay_session_id=replay_session_id,
            )
        else:
            await BatchRunCaseRepository(db).update(
                case,
                status=BatchCaseStatus.SUCCEEDED,
                output_text=output_text,
                diff_json=diff_payload,
                duration_ms=duration_ms,
                replay_session_id=replay_session_id,
            )
        await db.commit()


def _diff_texts(baseline: str, candidate: str) -> dict[str, Any]:
    """Produce a compact, UI-friendly diff summary.

    Returns ``{similarity: float, unified_diff: str, baseline_tokens: int,
    candidate_tokens: int}``. We cap ``unified_diff`` at 4 KB so a wildly
    different reply doesn't explode the DB row.
    """

    matcher = difflib.SequenceMatcher(a=baseline, b=candidate, autojunk=False)
    similarity = round(matcher.ratio(), 4)
    diff_lines = list(
        difflib.unified_diff(
            baseline.splitlines(),
            candidate.splitlines(),
            fromfile="baseline",
            tofile="candidate",
            n=1,
            lineterm="",
        )
    )
    unified = "\n".join(diff_lines)
    if len(unified) > 4096:
        unified = unified[:4096] + "\n…(truncated)"
    return {
        "similarity": similarity,
        "unified_diff": unified,
        "baseline_tokens": len(baseline.split()),
        "candidate_tokens": len(candidate.split()),
    }


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


__all__ = [
    "capture_checkpoint",
    "create_batch_run",
    "execute_batch",
    "fork_at_checkpoint",
]
