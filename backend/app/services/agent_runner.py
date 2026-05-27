"""Non-interactive agent runner — used by Channels (IM) and Flows.

The WebSocket path in ``app.api.v1.sessions`` streams RunEvents to a browser.
Channels and Flows don't have a browser — they want the full final answer +
some stats. This module drives the same backend but collects the result into
a simple ``AgentResult``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.registry import get_backend
from app.db.models.agent import BackendKind
from app.db.models.message import MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.agent import AgentRepository
from app.repositories.session import MessageRepository, SessionRepository
from app.services import inflight_run as inflight_svc
from app.services import session as sess_svc
from app.services import session_artifact as artifact_svc

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    final_text: str = ""
    tool_events: list[dict] = field(default_factory=list)
    usage_payload: dict = field(default_factory=dict)
    error: str | None = None
    session_id: uuid.UUID | None = None


async def run_agent_one_shot(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    user_text: str,
    iteration_budget: int = 8,
) -> AgentResult:
    """Run one turn for the given (session, agent) and persist the assistant
    message with the full final_text + tool events + token usage.

    Returns the collected output. The caller is responsible for commit().
    """
    result = AgentResult(session_id=session_id)
    run_id = uuid.uuid4()

    agent = await AgentRepository(db).get(agent_id)
    if agent is None:
        result.error = "agent_not_found"
        return result

    session_obj = await SessionRepository(db).get(session_id)
    if session_obj is None:
        result.error = "session_not_found"
        return result

    # Append the user message first so the history processor sees it.
    await sess_svc.append_message(
        db,
        session_obj=session_obj,
        role=MessageRole.USER,
        content_json={"text": user_text},
        author_identity_id=identity_id,
    )
    await db.flush()

    # Build history from persisted rows (last ~40 including the new user row).
    msg_rows = await MessageRepository(db).list_recent(session_id=session_id, limit=40)
    history: list[dict] = []
    for m in msg_rows:
        if m.role == MessageRole.USER:
            history.append({"role": "user", "content_json": m.content_json or {}})
        elif m.role == MessageRole.ASSISTANT:
            history.append({"role": "assistant", "content_json": m.content_json or {}})

    backend = get_backend(agent.backend_kind)
    if backend is None:
        # Fall back to the bundled native runtime so Kind mismatch still runs.
        backend = get_backend(BackendKind.NATIVE)
    if backend is None:
        result.error = "no_backend"
        return result

    req = RunRequest(
        run_id=run_id,
        workspace_id=workspace_id,
        agent_id=agent.id,
        session_id=session_id,
        identity_id=identity_id or uuid.UUID(int=0),
        user_text=user_text,
        message_history=history[:-1],  # exclude the just-appended user turn
        toolbox=[],
        skills=[],
        policy={
            "autonomy_level": agent.autonomy_level,
            "backend_adapter_id": (
                str(agent.backend_adapter_id)
                if getattr(agent, "backend_adapter_id", None)
                else None
            ),
            "code_mode": agent.metadata_json.get("code_mode"),
            "context": agent.metadata_json.get("context") or {},
            "skills": agent.metadata_json.get("skills"),
            "todos": agent.metadata_json.get("todos", True),
            "sandbox": agent.metadata_json.get("sandbox"),
            # Channels / Flows can't reasonably do HITL — force approvals off.
            "approvals": False,
            "default_search_provider_kind": agent.default_search_provider_kind,
            "persona_md": agent.persona_md,
            "workspace_id": str(workspace_id),
            "session_id": str(session_id),
        },
        iteration_budget=iteration_budget,
    )

    full_text_parts: list[str] = []
    collected_events: list[dict] = []
    raised_exc: BaseException | None = None
    event_seq = 0

    # M2.5.2 — register the top-level run spine so the backend restart
    # / heartbeat-timeout sweeps can flag this turn as LOST if the
    # process dies mid-flight. Best-effort; never blocks the run.
    await _inflight_register_safe(
        run_id=run_id,
        session_id=session_id,
        workspace_id=workspace_id,
        backend_kind=str(agent.backend_kind),
        agent_id=agent.id,
        identity_id=identity_id,
        request_snapshot=_runner_inflight_snapshot(req),
    )

    try:
        async for ev in backend.run(req):
            collected_events.append({"kind": ev.kind.value, "data": dict(ev.data)})
            event_seq += 1
            if ev.kind != RunEventKind.DELTA:
                await _inflight_heartbeat_safe(run_id=run_id, seq=event_seq)
            if ev.kind == RunEventKind.DELTA:
                full_text_parts.append(ev.data.get("text", ""))
            elif ev.kind in (RunEventKind.TOOL_CALL, RunEventKind.TOOL_RESULT):
                result.tool_events.append(ev.data)
            elif ev.kind == RunEventKind.USAGE:
                result.usage_payload = ev.data
            elif ev.kind == RunEventKind.ERROR:
                result.error = str(ev.data.get("message") or ev.data)
    except Exception as e:  # pragma: no cover
        log.exception("run_agent_one_shot failed")
        raised_exc = e
        result.error = str(e)
        await _inflight_finish_safe(
            run_id=run_id,
            state=inflight_svc.InflightRunState.FAILED,
            reason="run_agent_one_shot raised",
            error_kind=type(e).__name__[:80],
        )
    else:
        await _inflight_finish_safe(
            run_id=run_id,
            state=inflight_svc.InflightRunState.COMPLETED,
            reason="run_agent_one_shot finished",
        )

    result.final_text = "".join(full_text_parts)

    # Persist assistant message so /sessions/{id}/messages reflects the turn.
    tokens = result.usage_payload.get("tokens") or {}
    assistant_msg = await sess_svc.append_message(
        db,
        session_obj=session_obj,
        role=MessageRole.ASSISTANT,
        content_json={"text": result.final_text},
        author_agent_id=agent.id,
        tool_call_json=({"events": result.tool_events} if result.tool_events else None),
        token_usage_json=_usage_blob(result.usage_payload, tokens),
    )

    # M0.2 — repoint the FINAL frame's lineage at the persisted assistant
    # row so the artifact's last assistant turn survives prompt churn.
    for ev_dict in reversed(collected_events):
        if ev_dict.get("kind") == RunEventKind.FINAL.value:
            ev_dict["data"]["message_id"] = str(assistant_msg.id)
            break

    injected_pack_ids = _read_injected_skill_ids(backend, run_id)

    artifact_row = await artifact_svc.capture_from_run_outcome(
        db,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        agent_id=agent.id,
        identity_id=identity_id,
        user_text=user_text,
        events=collected_events,
        raised_exc=raised_exc,
        injected_skill_pack_ids=injected_pack_ids or None,
        finished_at=datetime.now(UTC).replace(tzinfo=None),
    )

    if artifact_row is not None and injected_pack_ids:
        await _record_skill_injection_usage(
            db,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent.id,
            identity_id=identity_id,
            pack_ids=injected_pack_ids,
        )

    # M0.3 — enqueue async judge for non-cancelled runs. Failures here
    # are non-fatal: a downed Redis must not break the channel/flow
    # turn. The audit row provides the breadcrumb instead.
    if artifact_row is not None and getattr(artifact_row, "final_outcome", None) != "cancelled":
        try:
            from app.worker.queue import enqueue

            await enqueue("judge_session_artifact", str(artifact_row.id), _defer_by=5)
        except Exception:
            log.exception(
                "judge enqueue failed for run %s artifact %s",
                run_id,
                artifact_row.id,
            )
            try:
                from app.services import audit as audit_svc

                await audit_svc.record(
                    db,
                    action="judge.enqueue_failed",
                    actor_identity_id=identity_id,
                    workspace_id=workspace_id,
                    resource_type="session_artifact",
                    resource_id=artifact_row.id,
                    summary="judge enqueue failed",
                    metadata={"artifact_id": str(artifact_row.id)},
                )
            except Exception:  # pragma: no cover
                log.exception("audit write for judge enqueue failure failed")

    # M0.7 — drain the cache-aware mutation buffer for this session.
    # Wrapped so a downed memory pipeline never breaks the channel/flow
    # turn; the workspace sweep cron is the backstop. Audit rows capture
    # both the success summary and any swallowed failure for replay.
    try:
        from app.services import audit as audit_svc
        from app.services import pending_memory as pending_memory_svc

        promote_result = await pending_memory_svc.promote_pending_memories_for_session(
            db,
            workspace_id=workspace_id,
            session_id=session_id,
            actor_identity_id=identity_id,
        )
        if promote_result["promoted"] or promote_result["failed"] or promote_result["skipped"]:
            await audit_svc.record(
                db,
                action="memory.promotion_completed",
                actor_identity_id=identity_id,
                workspace_id=workspace_id,
                resource_type="session",
                resource_id=session_id,
                summary=(
                    f"promoted={promote_result['promoted']} "
                    f"skipped={promote_result['skipped']} "
                    f"failed={promote_result['failed']}"
                ),
                metadata={
                    "session_id": str(session_id),
                    "trigger": "channel_capture_hook",
                    **promote_result,
                },
            )
    except Exception as exc:
        log.exception(
            "pending memory promote failed for run %s session %s",
            run_id,
            session_id,
        )
        try:
            from app.services import audit as audit_svc

            await audit_svc.record(
                db,
                action="memory.promotion_failed",
                actor_identity_id=identity_id,
                workspace_id=workspace_id,
                resource_type="session",
                resource_id=session_id,
                summary="channel promote hook raised — workspace sweep will retry",
                metadata={
                    "session_id": str(session_id),
                    "error_class": type(exc).__name__,
                    "trigger": "channel_capture_hook",
                },
            )
        except Exception:  # pragma: no cover
            log.exception("audit write for promote failure also failed")

    _ = SessionKind  # keep import
    return result


def _read_injected_skill_ids(backend, run_id: uuid.UUID) -> list[uuid.UUID]:
    """Pull the run's injected SkillPack id list off the backend.

    Returns an empty list when the backend does not expose the
    introspection hook (OpenClaw / remote adapters) or when the lookup
    raises — telemetry for that backend kind is intentionally absent.
    """
    if backend is None or not hasattr(backend, "get_injected_skill_ids"):
        return []
    try:
        ids = backend.get_injected_skill_ids(run_id) or []
    except Exception:
        log.warning(
            "skill.injection_lookup_failed",
            extra={"run_id": str(run_id)},
            exc_info=True,
        )
        return []
    return list(ids)


async def _record_skill_injection_usage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    pack_ids: list[uuid.UUID],
) -> None:
    """Best-effort SkillUsage(INJECTED) batch on the caller's session.

    Reuses the caller-provided ``db`` so the row joins the same
    transaction the artifact capture went into. A failure here writes
    ``audit_events(action="skill.usage_recording_failed")`` and never
    breaks the channel/flow lifecycle.
    """
    from app.db.models.skill_usage import SkillUsageEventKind
    from app.services import audit as audit_svc
    from app.services import skill_usage as skill_usage_svc

    try:
        await skill_usage_svc.record_usage_batch(
            db,
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
            event_kind=SkillUsageEventKind.INJECTED,
            pack_ids=pack_ids,
        )
    except Exception as exc:
        log.exception("skill usage record_usage_batch failed for run %s", run_id)
        try:
            await audit_svc.record(
                db,
                action="skill.usage_recording_failed",
                actor_identity_id=identity_id,
                workspace_id=workspace_id,
                resource_type="skill_run",
                resource_id=run_id,
                summary="skill usage batch insert failed",
                metadata={
                    "run_id": str(run_id),
                    "session_id": str(session_id),
                    "pack_count": len(pack_ids),
                    "error_class": type(exc).__name__,
                },
            )
        except Exception:  # pragma: no cover
            log.exception("audit write for skill usage recording failure also failed")


def _usage_blob(usage: dict, tokens: dict) -> dict:
    """Shape the channel/flow assistant-message usage blob.

    M2.5.7 — ``model`` is the **served** name (client-facing); the
    actual upstream id rides along on ``upstream_model`` when the
    workspace alias map redirected the call so audit / diagnostics
    queries can still recover it.
    """
    inp = int(tokens.get("input") or 0)
    out = int(tokens.get("output") or 0)
    cost = float(usage.get("cost") or 0.0)
    if inp == 0 and out == 0 and cost == 0.0:
        return {}
    served = usage.get("served_model") or usage.get("model")
    upstream = usage.get("upstream_model") or usage.get("model")
    blob: dict = {
        "input": inp,
        "output": out,
        "cost": cost,
        "cost_currency": usage.get("cost_currency") or "USD",
        "cost_matched_model": usage.get("cost_matched_model"),
        "latency_ms": int(usage.get("latency_ms") or 0),
        "provider": usage.get("provider"),
        "model": served,
    }
    if upstream and upstream != served:
        blob["upstream_model"] = upstream
    return blob


# ─── Inflight-run helpers (M2.5.2) ──────────────────────────
def _runner_inflight_snapshot(req) -> dict:
    """Trim the channel/flow ``RunRequest`` for the spine row JSONB."""
    return {
        "run_id": str(req.run_id),
        "agent_id": str(req.agent_id),
        "session_id": str(req.session_id),
        "user_text_chars": len(req.user_text or ""),
        "history_messages": len(req.message_history),
        "iteration_budget": req.iteration_budget,
        "model_override": req.model_override,
        "trigger": "agent_runner",
        "policy_keys": sorted((req.policy or {}).keys()),
    }


async def _inflight_register_safe(
    *,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    backend_kind: str,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    request_snapshot: dict,
) -> None:
    try:
        factory = get_session_factory()
        async with factory() as db:
            await inflight_svc.register_run(
                db,
                run_id=run_id,
                session_id=session_id,
                workspace_id=workspace_id,
                backend_kind=backend_kind,
                agent_id=agent_id,
                identity_id=identity_id,
                request_snapshot=request_snapshot,
            )
            await db.commit()
    except Exception:  # pragma: no cover - never block runner
        log.exception("inflight register failed run_id=%s", run_id)


async def _inflight_heartbeat_safe(*, run_id: uuid.UUID, seq: int) -> None:
    try:
        factory = get_session_factory()
        async with factory() as db:
            updated = await inflight_svc.update_last_seen(db, run_id=run_id, last_event_seq=seq)
            if updated:
                await db.commit()
    except Exception:  # pragma: no cover - heartbeat is best-effort
        log.debug("inflight heartbeat skipped run_id=%s", run_id, exc_info=True)


async def _inflight_finish_safe(
    *,
    run_id: uuid.UUID,
    state: inflight_svc.InflightRunState,
    reason: str | None = None,
    error_kind: str | None = None,
) -> None:
    try:
        factory = get_session_factory()
        async with factory() as db:
            await inflight_svc.transition(
                db,
                run_id=run_id,
                target_state=state,
                reason=reason,
                error_kind=error_kind,
            )
            await db.commit()
    except Exception:  # pragma: no cover - never block runner
        log.exception("inflight transition failed run_id=%s", run_id)


async def ensure_channel_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    channel_id: uuid.UUID,
    thread_key: str,
    subject_id: uuid.UUID,
    title_hint: str | None = None,
) -> SessionModel:
    """Get-or-create a Session for an IM thread — keyed by the channel +
    external thread key.

    Subsequent messages from the same IM thread land on the same Session so
    the Agent sees prior turns.
    """
    # Find an existing session for this channel+thread.
    existing = await SessionRepository(db).find_channel_session(
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_key=thread_key,
    )
    if existing is not None:
        return existing

    new_session = await SessionRepository(db).create(
        workspace_id=workspace_id,
        kind=SessionKind.CHANNEL,
        subject_id=subject_id,
        channel_id=channel_id,
        title=title_hint or thread_key,
        metadata_json={"thread_key": thread_key},
    )
    return new_session
