"""Async LLM judge jobs (M0.1 goal alignment + M0.3 run-quality verdict).

Both jobs share the same playbook:

1. Resolve the active aux model via :func:`auxiliary_client.get_aux_model`.
2. Issue one structured aux call with a tight system prompt.
3. Persist the result + a small audit breadcrumb.
4. Fall through to a degraded path when the per-workspace breaker is
   open (5 consecutive failures within 5 minutes for the judge bucket;
   3 within 60 s for the older alignment bucket).

The breaker primitives live in :mod:`app.jobs._breaker` so adding a
third aux-LLM job in a future milestone is one import away.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.agents.auxiliary_client import (
    AuxiliaryTask,
    JudgeVerdict,
    call_aux_chat,
    call_aux_judge,
    get_aux_model,
    get_workspace_aux_settings,
)
from app.db.session import get_session_factory
from app.jobs._breaker import (
    bump_failure,
    consume_rate,
    is_breaker_open,
    reset_failure,
)

log = logging.getLogger(__name__)


# ─── Aux LLM response schema (M0.1 goal alignment) ───────────
class _AlignmentResponse(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=600)


_PROMPT_SYSTEM = (
    "You judge whether an assistant message advances a stated goal. "
    "Output strict JSON: {\"score\": float in [0,1], \"rationale\": short single sentence}. "
    "Score 1.0 means the message materially advances the goal; "
    "0.0 means it is off-topic or actively diverging. "
    "Use the language of the goal text."
)


def _build_user_prompt(
    *, goal_text: str, criteria: list[str], assistant_text: str
) -> str:
    crit_block = "\n".join(f"- {c}" for c in criteria) or "(none provided)"
    return (
        f"GOAL:\n{goal_text}\n\n"
        f"SUCCESS CRITERIA:\n{crit_block}\n\n"
        f"ASSISTANT MESSAGE:\n{assistant_text or '(empty)'}"
    )


# ─── M0.1 alignment breaker (legacy bucket) ──────────────────
_ALIGN_BUCKET = "judge"
_ALIGN_BREAKER_WINDOW_S = 60
_ALIGN_BREAKER_TRIP_AT = 3


async def _bump_failure_counter(workspace_id: str) -> int:
    return await bump_failure(
        bucket=_ALIGN_BUCKET,
        workspace_id=workspace_id,
        window_seconds=_ALIGN_BREAKER_WINDOW_S,
    )


async def _reset_failure_counter(workspace_id: str) -> None:
    await reset_failure(bucket=_ALIGN_BUCKET, workspace_id=workspace_id)


# ─── ARQ task ────────────────────────────────────────────────
async def score_message_alignment(
    ctx: dict[str, Any],
    session_goal_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Score one assistant message against the locked goal.

    Returns a small dict (job result store) and persists a
    ``GoalAlignmentScore`` row. Notification wiring (in-app + email) is
    a TODO marker for M0.10 — see ``audit_events`` action
    ``goal.alignment_low``.
    """
    factory = get_session_factory()
    goal_uid = uuid.UUID(session_goal_id)
    msg_uid = uuid.UUID(message_id)

    async with factory() as db:
        from app.repositories.session import MessageRepository
        from app.repositories.session_goal import SessionGoalRepository

        goal = await SessionGoalRepository(db).get(goal_uid)
        if goal is None:
            return {"status": "skipped_goal_missing"}
        if goal.unlocked_at is not None:
            return {"status": "skipped_goal_unlocked"}

        msg = await MessageRepository(db).get(msg_uid)
        if msg is None or msg.workspace_id != goal.workspace_id:
            return {"status": "skipped_message_missing"}

        assistant_text = ""
        if isinstance(msg.content_json, dict):
            t = msg.content_json.get("text")
            if isinstance(t, str):
                assistant_text = t
        workspace_id = goal.workspace_id

    score: float
    rationale: str
    judged_by_model: str
    breaker_tripped = False

    async with factory() as db:
        config = await get_aux_model(
            db,
            workspace_id=workspace_id,
            task=AuxiliaryTask.GOAL_ALIGNMENT,
        )

    if config is None:
        # No aux model at all → degrade silently to mid-range and audit
        # so admins can spot the misconfiguration. Not a failure (do
        # not bump retries / breaker).
        score = 0.5
        rationale = "No auxiliary model configured; default neutral score."
        judged_by_model = "heuristic:no_aux"
    else:
        try:
            response = await call_aux_chat(
                config=config,
                system=_PROMPT_SYSTEM,
                user=_build_user_prompt(
                    goal_text=goal.goal_text,
                    criteria=list(goal.success_criteria or []),
                    assistant_text=assistant_text,
                ),
                response_format=_AlignmentResponse,
            )
        except Exception as exc:  # pragma: no cover
            log.exception("aux call raised for goal=%s msg=%s", goal_uid, msg_uid)
            response = None
            ctx_exc: BaseException | None = exc
        else:
            ctx_exc = None

        if isinstance(response, _AlignmentResponse):
            await _reset_failure_counter(str(workspace_id))
            score = float(max(0.0, min(1.0, response.score)))
            rationale = response.rationale.strip() or ""
            judged_by_model = config.model
        else:
            failures = await _bump_failure_counter(str(workspace_id))
            if failures >= _ALIGN_BREAKER_TRIP_AT:
                breaker_tripped = True
                score = 0.5
                rationale = (
                    f"Aux scorer degraded after {failures} consecutive failures."
                )
                judged_by_model = "heuristic:breaker"
            else:
                # Surface the failure so ARQ retries within the budget.
                # The retry path will re-attempt aux; only when budget
                # exhausts does ``on_job_failed`` (worker hook) write
                # the ``job.failed_permanent`` audit row.
                if ctx_exc is not None:
                    raise ctx_exc
                raise RuntimeError("aux scoring returned no parseable response")

    async with factory() as db:
        from app.services import audit as audit_svc
        from app.services import notification_events as notif_events
        from app.services import session_goal as goal_svc

        row = await goal_svc.record_score(
            db,
            session_goal_id=goal_uid,
            message_id=msg_uid,
            workspace_id=workspace_id,
            score=score,
            rationale=rationale,
            judged_by_model=judged_by_model,
        )

        if breaker_tripped:
            await audit_svc.record(
                db,
                action="judge.degraded",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="session_goal",
                resource_id=goal_uid,
                summary="Aux scorer degraded after consecutive failures",
                metadata={
                    "window_s": _ALIGN_BREAKER_WINDOW_S,
                    "trip_at": _ALIGN_BREAKER_TRIP_AT,
                    "task": "score_message_alignment",
                },
            )
            try:
                await notif_events.emit_event(
                    db,
                    event_key="judge.degraded",
                    workspace_id=workspace_id,
                    cooldown_resource_id=str(workspace_id),
                    payload={
                        "task": "score_message_alignment",
                        "session_id": str(goal.session_id),
                    },
                )
            except Exception:  # pragma: no cover - notification best-effort
                log.exception("notify judge.degraded failed for ws=%s", workspace_id)
        if row.flagged:
            await audit_svc.record(
                db,
                action="goal.alignment_low",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="session_goal",
                resource_id=goal_uid,
                summary=(
                    f"Alignment score {score:.2f} below threshold "
                    f"{goal.alignment_threshold:.2f} for message {msg_uid}"
                ),
                metadata={
                    "session_id": str(goal.session_id),
                    "message_id": str(msg_uid),
                    "score": score,
                    "threshold": goal.alignment_threshold,
                },
            )
            try:
                await notif_events.emit_event(
                    db,
                    event_key="goal.alignment_low",
                    workspace_id=workspace_id,
                    cooldown_resource_id=str(goal_uid),
                    payload={
                        "session_id": str(goal.session_id),
                        "message_id": str(msg_uid),
                        "score": score,
                        "threshold": goal.alignment_threshold,
                        "goal_text": goal.goal_text[:120],
                        "session_label": str(goal.session_id)[:8],
                    },
                )
            except Exception:  # pragma: no cover - notification best-effort
                log.exception(
                    "notify goal.alignment_low failed for goal=%s", goal_uid
                )

        await db.commit()

    return {
        "status": "scored",
        "session_goal_id": str(goal_uid),
        "message_id": str(msg_uid),
        "score": score,
        "flagged": score < goal.alignment_threshold,
        "judged_by_model": judged_by_model,
        "degraded": breaker_tripped,
    }


# ─── ARQ worker hooks ────────────────────────────────────────
async def on_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """ARQ hook for jobs that exhausted their retry budget.

    Writes one ``audit_events(action="job.failed_permanent")`` so
    operators can spot dead-letter scoring runs without trawling Redis.
    Best-effort; never re-raises.
    """
    try:
        from app.services import audit as audit_svc

        function_name = ctx.get("function") or "unknown"
        job_id = ctx.get("job_id")
        # The args are stashed by ARQ in ``job_try`` / ``args`` depending
        # on version; both are best-effort.
        args = ctx.get("args") or []
        workspace_id = None
        # Try to associate to a workspace via the SessionGoal target so
        # the audit feed surfaces it on the right tenant timeline.
        if function_name == score_message_alignment.__name__ and args:
            try:
                goal_uid = uuid.UUID(str(args[0]))
                async with get_session_factory()() as db:
                    from app.repositories.session_goal import SessionGoalRepository

                    goal = await SessionGoalRepository(db).get(goal_uid)
                    if goal is not None:
                        workspace_id = goal.workspace_id
            except Exception:  # pragma: no cover
                pass
        elif function_name == "judge_session_artifact" and args:
            try:
                artifact_uid = uuid.UUID(str(args[0]))
                async with get_session_factory()() as db:
                    from app.repositories.session_artifact import (
                        SessionArtifactRepository,
                    )

                    art = await SessionArtifactRepository(db).get(artifact_uid)
                    if art is not None:
                        workspace_id = art.workspace_id
            except Exception:  # pragma: no cover
                pass

        async with get_session_factory()() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="job",
                resource_id=None,
                summary=f"job {function_name} failed permanently: {exc!r}",
                metadata={
                    "function": function_name,
                    "job_id": job_id,
                    "args": [str(a) for a in args],
                    "exception": repr(exc),
                },
            )
            try:
                from app.services import notification_events as notif_events

                await notif_events.emit_event(
                    db,
                    event_key="job.failed_permanent",
                    workspace_id=workspace_id,
                    cooldown_resource_id=str(job_id) if job_id else function_name,
                    payload={
                        "function": function_name,
                        "job_id": str(job_id) if job_id else None,
                        "exception": repr(exc)[:200],
                    },
                )
            except Exception:  # pragma: no cover
                log.exception(
                    "notify job.failed_permanent failed for %s", function_name
                )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_job_failed_permanent hook crashed")


# ─── M0.3 — run-quality judge ────────────────────────────────
_JUDGE_BUCKET = "judge_run"


def _defer(seconds: int) -> Exception:
    """Build an ARQ ``Retry`` signal that defers without consuming retries.

    Falls back to a plain ``RuntimeError`` when the running ARQ version
    doesn't expose ``Retry`` — the breaker will eventually catch
    misbehaving workspaces.
    """
    try:
        from arq.worker import Retry  # type: ignore[attr-defined]

        return Retry(defer=int(seconds))
    except Exception:  # pragma: no cover
        return RuntimeError(f"defer:{int(seconds)}s")


async def _read_judge_settings(workspace_id: uuid.UUID) -> dict[str, Any]:
    """Snapshot of the workspace ``aux`` config used by both judge jobs."""
    factory = get_session_factory()
    async with factory() as db:
        return await get_workspace_aux_settings(db, workspace_id=workspace_id)


async def _emit_notification_safely(
    *,
    event_key: str,
    workspace_id: uuid.UUID | None,
    cooldown_resource_id: str | None,
    payload: dict[str, Any],
) -> None:
    """Best-effort notification emit for judge audits run on fresh sessions.

    Mirrors :func:`_audit_judge` so the judge pipeline never raises
    notification errors back to ARQ. The fan-out opens its own DB
    session because the surrounding ``_audit_judge`` calls already
    scoped theirs.
    """
    try:
        from app.services import notification_events as notif_events

        async with get_session_factory()() as db:
            await notif_events.emit_event(
                db,
                event_key=event_key,
                workspace_id=workspace_id,
                cooldown_resource_id=cooldown_resource_id,
                payload=payload,
            )
            await db.commit()
    except Exception:  # pragma: no cover - notification best-effort
        log.exception("emit_event %s failed for ws=%s", event_key, workspace_id)


async def _audit_judge(
    *,
    workspace_id: uuid.UUID | None,
    action: str,
    artifact_id: uuid.UUID | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """One-line audit helper for the judge pipeline.

    Always opens its own DB session so it stays usable from ARQ frames
    where the caller's ``factory()`` context already exited.
    """
    try:
        from app.services import audit as audit_svc

        async with get_session_factory()() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="session_artifact",
                resource_id=artifact_id,
                summary=summary,
                metadata=metadata or {},
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("judge audit failed action=%s", action)


async def judge_session_artifact(
    ctx: dict[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    """Score one captured artifact on the 1 / 0 / -1 scale.

    Behaviour:

    * Cancelled artifacts skip judge entirely (token-saving).
    * Already-judged artifacts (re-enqueued by the periodic sweep) are
      a no-op.
    * If the per-workspace rate budget is exhausted we raise
      :class:`_DeferRequested`; ARQ retries the job after the
      configured backoff so the budget can replenish.
    * If the breaker is open the verdict is forced to ``score=0`` with
      ``degraded=True`` and audit ``judge.degraded`` is written.
    * Aux failures bump the breaker; retries are surfaced so ARQ can
      try the same artifact up to ``max_tries`` before
      ``on_job_end`` writes ``job.failed_permanent``.
    """
    factory = get_session_factory()
    art_uid = uuid.UUID(str(artifact_id))

    async with factory() as db:
        from app.repositories.session_artifact import SessionArtifactRepository

        artifact = await SessionArtifactRepository(db).get(art_uid)
        if artifact is None or artifact.deleted_at is not None:
            return {"status": "skipped_missing"}

        workspace_id = artifact.workspace_id
        artifact_snapshot: dict[str, Any] = {
            "id": artifact.id,
            "workspace_id": workspace_id,
            "final_outcome": artifact.final_outcome,
            "error_kind": artifact.error_kind,
            "iteration_count": artifact.iteration_count,
            "invoked_tools": list(artifact.invoked_tools or []),
            "turns_json": list(artifact.turns_json or []),
            "judge_score": artifact.judge_score,
        }

    if artifact_snapshot["final_outcome"] == "cancelled":
        await _audit_judge(
            workspace_id=workspace_id,
            artifact_id=art_uid,
            action="judge.skipped_cancelled",
            summary="cancelled run skipped by judge",
            metadata={"final_outcome": "cancelled"},
        )
        return {"status": "skipped_cancelled"}

    if artifact_snapshot["judge_score"] is not None:
        await _audit_judge(
            workspace_id=workspace_id,
            artifact_id=art_uid,
            action="judge.skipped_already",
            summary="artifact already has a judge score",
            metadata={"existing_score": artifact_snapshot["judge_score"]},
        )
        return {"status": "skipped_already"}

    settings = await _read_judge_settings(workspace_id)
    fail_strikes = int(settings.get("judge_fail_strikes") or 5)
    fail_window = int(settings.get("judge_fail_window_seconds") or 300)
    fail_recover = int(settings.get("judge_breaker_recover_seconds") or 3600)
    rate_per_minute = int(settings.get("judge_rate_per_minute") or 60)
    turns_chars = int(settings.get("judge_turns_serialized_chars") or 12000)
    prompt_max = int(settings.get("judge_prompt_max_chars") or 800)

    breaker_open = await is_breaker_open(
        bucket=_JUDGE_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=fail_strikes,
    )

    if breaker_open:
        async with factory() as db:
            from app.services import judge as judge_svc

            await judge_svc.persist_verdict(
                db,
                workspace_id=workspace_id,
                artifact_id=art_uid,
                score=0,
                confidence=0.0,
                rationale="aux scorer breaker open",
                process_notes=[
                    f"breaker_open strikes>={fail_strikes}",
                ],
                judged_by_model=None,
                latency_ms=None,
                degraded=True,
            )
            await db.commit()
        await _audit_judge(
            workspace_id=workspace_id,
            artifact_id=art_uid,
            action="judge.degraded",
            summary="judge breaker open; wrote 0.0 placeholder",
            metadata={
                "fail_strikes": fail_strikes,
                "fail_window_seconds": fail_window,
                "recover_seconds": fail_recover,
                "task": "judge_session_artifact",
            },
        )
        await _emit_notification_safely(
            event_key="judge.degraded",
            workspace_id=workspace_id,
            cooldown_resource_id=str(workspace_id),
            payload={
                "task": "judge_session_artifact",
                "artifact_id": str(art_uid),
            },
        )
        return {
            "status": "degraded",
            "artifact_id": str(art_uid),
            "score": 0,
        }

    allowed = await consume_rate(
        bucket=_JUDGE_BUCKET,
        workspace_id=str(workspace_id),
        limit=rate_per_minute,
        period_seconds=60,
    )
    if not allowed:
        # Rate limit hit is normal pressure, not an aux failure: defer
        # via ARQ ``Retry`` so the retry budget is preserved and the
        # sliding window has a chance to drop older calls.
        raise _defer(seconds=20)

    from app.agents.auxiliary_client import (
        _serialise_artifact_turns as _serialise_turns_internal,
    )

    turns_serialized = _serialise_turns_internal(
        artifact_snapshot["turns_json"], max_chars=turns_chars
    )

    class _Stub:
        pass

    stub_artifact = _Stub()
    stub_artifact.final_outcome = artifact_snapshot["final_outcome"]
    stub_artifact.error_kind = artifact_snapshot["error_kind"]
    stub_artifact.iteration_count = artifact_snapshot["iteration_count"]
    stub_artifact.invoked_tools = artifact_snapshot["invoked_tools"]

    started = time.monotonic()
    async with factory() as db:
        verdict, config = await call_aux_judge(
            db,
            workspace_id=workspace_id,
            artifact=stub_artifact,
            turns_serialized=turns_serialized,
            prompt_max_chars=prompt_max,
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    if not isinstance(verdict, JudgeVerdict):
        # Aux failure / unparseable → bump breaker and re-raise so ARQ
        # retries within the budget.
        failures = await bump_failure(
            bucket=_JUDGE_BUCKET,
            workspace_id=str(workspace_id),
            window_seconds=fail_window,
            recover_seconds=fail_recover,
        )
        if config is None:
            await _audit_judge(
                workspace_id=workspace_id,
                artifact_id=art_uid,
                action="judge.degraded",
                summary="no aux model configured for judge task",
                metadata={
                    "fail_strikes": fail_strikes,
                    "task": "judge_session_artifact",
                },
            )
            await _emit_notification_safely(
                event_key="judge.degraded",
                workspace_id=workspace_id,
                cooldown_resource_id=str(workspace_id),
                payload={
                    "task": "judge_session_artifact",
                    "reason": "aux_unconfigured",
                    "artifact_id": str(art_uid),
                },
            )
            async with factory() as db:
                from app.services import judge as judge_svc

                await judge_svc.persist_verdict(
                    db,
                    workspace_id=workspace_id,
                    artifact_id=art_uid,
                    score=0,
                    confidence=0.0,
                    rationale="no aux model configured",
                    process_notes=["aux_unconfigured"],
                    judged_by_model=None,
                    latency_ms=latency_ms,
                    degraded=True,
                )
                await db.commit()
            return {"status": "degraded", "artifact_id": str(art_uid)}
        raise RuntimeError(
            f"aux judge produced no verdict (failures={failures})"
        )

    await reset_failure(bucket=_JUDGE_BUCKET, workspace_id=str(workspace_id))

    async with factory() as db:
        from app.services import judge as judge_svc

        await judge_svc.persist_verdict(
            db,
            workspace_id=workspace_id,
            artifact_id=art_uid,
            score=int(verdict.score),
            confidence=float(verdict.confidence),
            rationale=verdict.rationale,
            process_notes=list(verdict.process_notes),
            error_kind_hint=verdict.error_kind_hint,
            judged_by_model=config.model if config else None,
            latency_ms=latency_ms,
            degraded=False,
        )
        await db.commit()

    await _audit_judge(
        workspace_id=workspace_id,
        artifact_id=art_uid,
        action="judge.completed",
        summary=f"judged artifact score={verdict.score}",
        metadata={
            "score": int(verdict.score),
            "confidence": float(verdict.confidence),
            "judged_by_model": config.model if config else None,
            "latency_ms": latency_ms,
        },
    )

    if int(verdict.score) == -1:
        await _emit_notification_safely(
            event_key="judge.score_negative",
            workspace_id=workspace_id,
            cooldown_resource_id=str(art_uid),
            payload={
                "artifact_id": str(art_uid),
                "score": int(verdict.score),
                "confidence": float(verdict.confidence),
                "rationale": (verdict.rationale or "")[:200],
            },
        )

    return {
        "status": "scored",
        "artifact_id": str(art_uid),
        "score": int(verdict.score),
        "confidence": float(verdict.confidence),
        "judged_by_model": config.model if config else None,
        "latency_ms": latency_ms,
        "degraded": False,
    }


async def judge_periodic_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hourly sweep: enqueue a judge call for any artifact missing a score.

    Walks every non-deleted workspace; for each, asks the artifact
    repo for the oldest unjudged batch (oldest-first FIFO so backlog
    can't starve historical runs). Cancelled artifacts and any with a
    completion age below ``min_age_seconds`` are skipped (the latter
    matches the live-enqueue defer of 5 s + a buffer so a sweep racing
    a fresh capture doesn't double-enqueue).

    Workspaces whose breaker is currently open are skipped entirely —
    the live-enqueue path will pick them up automatically when the
    breaker auto-recovers.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.security import utcnow_naive
    from app.db.models.session_artifact import SessionArtifact
    from app.db.models.workspace import Workspace

    factory = get_session_factory()
    min_age_seconds = 300  # 5 min — matches the M0.3 design spec.
    cutoff = utcnow_naive() - timedelta(seconds=min_age_seconds)

    enqueued = 0
    skipped_degraded = 0
    workspaces_seen = 0

    async with factory() as db:
        ws_rows = (
            (
                await db.execute(
                    select(Workspace.id).where(Workspace.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )

    for ws_id in ws_rows:
        workspaces_seen += 1
        settings = await _read_judge_settings(ws_id)
        fail_strikes = int(settings.get("judge_fail_strikes") or 5)
        if await is_breaker_open(
            bucket=_JUDGE_BUCKET,
            workspace_id=str(ws_id),
            trip_at=fail_strikes,
        ):
            skipped_degraded += 1
            continue

        async with factory() as db:
            from app.repositories.session_artifact import SessionArtifactRepository

            repo = SessionArtifactRepository(db)
            stmt = (
                select(SessionArtifact)
                .where(
                    SessionArtifact.workspace_id == ws_id,
                    SessionArtifact.deleted_at.is_(None),
                    SessionArtifact.judge_score.is_(None),
                    SessionArtifact.final_outcome != "cancelled",
                    SessionArtifact.finished_at <= cutoff,
                )
                .order_by(SessionArtifact.finished_at.asc())
                .limit(50)
            )
            artifacts = (await db.execute(stmt)).scalars().all()
            _ = repo  # repo retained for future enrichment hooks

        for art in artifacts:
            try:
                from app.worker.queue import enqueue

                await enqueue(
                    "judge_session_artifact", str(art.id), _defer_by=2
                )
                enqueued += 1
            except Exception:  # pragma: no cover
                log.exception(
                    "periodic sweep: enqueue failed for artifact %s", art.id
                )

    return {
        "status": "swept",
        "workspaces_seen": workspaces_seen,
        "enqueued": enqueued,
        "skipped_degraded": skipped_degraded,
    }
