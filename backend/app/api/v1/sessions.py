"""Session + Message routes + WebSocket streaming.

WebSocket protocol (bidirectional JSON frames):

  client -> server:
    {"type": "user_message", "data": {"text": "..."}}
    {"type": "ping"}
    {"type": "cancel", "data": {"run_id": "..."}}
    {"type": "approval_decision",
     "data": {"approval_id": "...", "action": "approve" | "deny",
              "reason": "optional note"}}

  server -> client:
    delta / thinking / tool_call / tool_result / approval_request /
    approval_update / usage / error / final / pong

On ``cancel``: in-flight turn task is cancelled AND every pending approval
belonging to this session (optionally filtered by ``run_id``) gets flipped to
status=CANCELLED, signalled on ``APPROVAL_MANAGER`` so the runner callback
returns False promptly, and broadcast to the client as ``approval_update``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from app.agents.harness.approvals import APPROVAL_MANAGER
from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.registry import get_backend
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.core.security import decode_token, utcnow_naive
from app.db.models.agent import BackendKind
from app.db.models.message import MessageRole
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.agent import AgentRepository
from app.repositories.approval import ApprovalRepository
from app.repositories.message_rating import MessageRatingRepository
from app.repositories.session import MessageRepository, SessionRepository
from app.repositories.squad import SquadMemberRepository, SquadRepository
from app.schemas.message_rating import (
    MessageRatingCreate,
    MessageRatingRead,
    MessageRatingSummary,
)
from app.schemas.session import (
    MessageCreate,
    MessageRead,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    StarSessionOut,
)
from app.schemas.session_goal import (
    GoalAlignmentScoreRead,
    SessionGoalCreate,
    SessionGoalRead,
    SessionGoalUpdate,
)
from app.schemas.session_share import (
    PublicSessionMessage,
    PublicSharedSession,
    SessionShareCreate,
    SessionShareList,
    SessionShareRead,
)
from app.services import inflight_run as inflight_svc
from app.services import message_rating as rating_svc
from app.services import session as svc
from app.services import session_goal as goal_svc
from app.services import session_share as share_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter()

# Strong refs to background tasks (e.g. AI title upgrade) so the asyncio
# event loop's GC doesn't reap them before they complete. Each task removes
# itself via ``add_done_callback``.
_BG_TASKS: set[asyncio.Task[Any]] = set()


# ─── Inflight-run helpers (M2.5.2) ──────────────────────────
async def _inflight_register(
    *,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    backend_kind: str,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID,
    request_snapshot: dict[str, Any],
) -> None:
    """Best-effort register of one ``inflight_runs`` row.

    Opens its own session so the chat turn's transactions are never
    coupled to lifecycle bookkeeping, and never raises into the WS
    path — a degraded recovery spine must not break a live chat.
    """
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
    except Exception:  # pragma: no cover - never block chat
        log.exception("inflight register failed run_id=%s", run_id)


async def _inflight_heartbeat(*, run_id: uuid.UUID, seq: int) -> None:
    """Best-effort ``last_seen_at`` bump. Skipped silently on failure."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            updated = await inflight_svc.update_last_seen(db, run_id=run_id, last_event_seq=seq)
            if updated:
                await db.commit()
    except Exception:  # pragma: no cover - heartbeat is best-effort
        log.debug("inflight heartbeat skipped run_id=%s", run_id, exc_info=True)


async def _inflight_finish(
    *,
    run_id: uuid.UUID,
    state: inflight_svc.InflightRunState,
    reason: str | None = None,
    error_kind: str | None = None,
) -> None:
    """Best-effort terminal transition. Never raises."""
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
    except Exception:  # pragma: no cover - never block chat
        log.exception("inflight transition failed run_id=%s", run_id)


def _build_inflight_snapshot(req: RunRequest, *, mode: str | None) -> dict[str, Any]:
    """Trim a ``RunRequest`` into the JSONB-safe shape the spine row stores.

    Drops attachment bytes (kept on the user message) and trims history
    so the row stays under a few KB even for chatty sessions.
    """
    return {
        "run_id": str(req.run_id),
        "agent_id": str(req.agent_id),
        "session_id": str(req.session_id),
        "identity_id": str(req.identity_id),
        "user_text_chars": len(req.user_text or ""),
        "history_messages": len(req.message_history),
        "iteration_budget": req.iteration_budget,
        "model_override": req.model_override,
        "mode": mode,
        "policy_keys": sorted((req.policy or {}).keys()),
        "attachments": [
            {
                "kind": str(a.get("kind") or ""),
                "mime_type": str(a.get("mime_type") or ""),
                "size_bytes": (
                    len(a["data"]) if isinstance(a.get("data"), (bytes, bytearray)) else None
                ),
            }
            for a in (req.attachments or [])
            if isinstance(a, dict)
        ],
    }


# Maximum number of cached events kept per WebSocket connection for
# replay-on-reconnect. Sized for typical chat turns (≤ 200 frames including
# token deltas) with headroom; the `deque(maxlen=...)` evicts oldest first
# when the cap is hit. v1 is in-process only — Redis-backed pub/sub for
# horizontal-scale replay is a P3 follow-up.
_WS_EVENT_CACHE_SIZE = 500


class _WsState(TypedDict):
    """Per-connection state shared between the receive loop, the turn task,
    the approval pump, and any background broadcast (e.g. AI title)."""

    seq: int  # monotonic counter; next emitted frame gets seq+1
    cache: deque[dict[str, Any]]  # ring buffer of the last N emitted frames
    current_run_id: uuid.UUID | None  # active run for cache lookup / cancel filtering
    send_lock: asyncio.Lock  # serialise concurrent senders (turn task + pump)


def _new_ws_state() -> _WsState:
    return {
        "seq": 0,
        "cache": deque(maxlen=_WS_EVENT_CACHE_SIZE),
        "current_run_id": None,
        "send_lock": asyncio.Lock(),
    }


async def _emit(
    websocket: WebSocket,
    ws_state: _WsState,
    payload: dict[str, Any],
    *,
    cache: bool = True,
) -> dict[str, Any]:
    """Send a frame to the client, stamping a monotonic ``seq`` (back-compat:
    older clients can ignore the extra field) and optionally retaining a copy
    in the connection's replay cache.

    Returns the *outgoing* payload (with seq) so callers can react to the
    assigned sequence number when needed.

    ``cache=False`` is for transient frames a reconnecting client doesn't
    need to receive twice (``pong`` / ``resume_ack``).
    """
    async with ws_state["send_lock"]:
        ws_state["seq"] += 1
        seq = ws_state["seq"]
        # Mutate a copy so the caller's dict isn't shared with the cache.
        out = dict(payload)
        data = dict(out.get("data") or {})
        data.setdefault("seq", seq)
        out["data"] = data
        # Tag with the active run id when the caller didn't already do it,
        # so a reconnect with ``last_seen_seq`` + ``run_id`` can filter the
        # replay to a specific run.
        run_id = ws_state.get("current_run_id")
        if run_id is not None and "run_id" not in data:
            data["run_id"] = str(run_id)
        if cache:
            ws_state["cache"].append(out)
        try:
            await websocket.send_json(out)
        except Exception:
            # Caller decides how to react; we surface but don't swallow.
            raise
        return out


async def _replay_cached_events(
    websocket: WebSocket,
    *,
    ws_state: _WsState,
    last_seen_seq: int | None,
    run_id: uuid.UUID | None,
) -> int:
    """Re-send every cached frame whose ``seq > last_seen_seq``.

    Optionally narrows by ``run_id``: useful when the client only cares about
    catching up the in-flight turn after a transient reconnect, not earlier
    completed turns. Returns the count of frames replayed.
    """
    if not ws_state["cache"]:
        return 0
    threshold = last_seen_seq if isinstance(last_seen_seq, int) else 0
    target = str(run_id) if run_id is not None else None
    replayed = 0
    for frame in list(ws_state["cache"]):
        data = frame.get("data") or {}
        seq = data.get("seq")
        if not isinstance(seq, int) or seq <= threshold:
            continue
        if target is not None and data.get("run_id") and data.get("run_id") != target:
            continue
        try:
            await websocket.send_json(frame)
            replayed += 1
        except Exception:  # pragma: no cover
            log.exception("ws replay failed at seq=%s", seq)
            break
    return replayed


def _slugify(name: str, *, fallback: str = "member") -> str:
    """Best-effort slug for subagent name (identifier-like).

    Strips non-identifier chars. If the resulting string is empty or reduces to
    the generic `fallback` (common for Chinese-only names), returns empty so the
    caller can fall back to a UUID-based identifier.
    """
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip())
    s = s.strip("_").lower()[:48]
    if s in ("", fallback):
        return ""
    return s


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── /goal slash command parser (M0.1) ──────────────────────
# Recognised shapes (case-insensitive command, text preserved verbatim):
#   "/goal"              → status (show currently locked goal, or "none")
#   "/goal unlock"       → unlock the active goal (no-op if none)
#   "/goal <text>"       → lock a fresh goal with default threshold/criteria
#
# Returned tuple: (action, payload). ``payload`` is the goal text for
# ``lock`` actions; empty string for ``status`` / ``unlock``. ``None``
# is returned for any text that isn't a recognised /goal command so the
# caller can fall through to the regular agent loop unchanged.
_GOAL_LOCK_RE = re.compile(r"^\s*/goal\s+(?P<text>.+?)\s*$", re.IGNORECASE | re.DOTALL)
_GOAL_STATUS_RE = re.compile(r"^\s*/goal(?:\s+status)?\s*$", re.IGNORECASE)
_GOAL_UNLOCK_RE = re.compile(r"^\s*/goal\s+unlock\s*$", re.IGNORECASE)


def _parse_goal_slash_command(text: str) -> tuple[str, str] | None:
    """Return ``(action, payload)`` for /goal commands; None otherwise.

    ``action`` is one of ``"status"``, ``"unlock"``, ``"lock"``. For
    ``"lock"`` the payload is the trimmed goal text (1..2000 chars
    enforced upstream by the service layer).
    """
    if not text or not text.lstrip().lower().startswith("/goal"):
        return None
    if _GOAL_STATUS_RE.match(text):
        return ("status", "")
    if _GOAL_UNLOCK_RE.match(text):
        return ("unlock", "")
    m = _GOAL_LOCK_RE.match(text)
    if m:
        body = m.group("text").strip()
        if not body:
            return ("status", "")
        return ("lock", body)
    return None


# ─── /insights slash command parser (M4.5) ───────────────────
# Recognised shapes (case-insensitive command, leading/trailing space
# is allowed):
#
#   "/insights"             → default day window (workspace's
#                             ``InsightsSettings.default_days``)
#   "/insights --days N"    → custom window (1 ≤ N ≤ max_days)
#   "/insights --days=N"    → equivalent
#
# The pure parser proxies through to
# :func:`app.services.cross_session_insights.parse_insights_command` so
# unit tests can hit the parser without importing the WS handler. The
# proxy is sync via ``asyncio.run_coroutine_threadsafe``-free pattern:
# the service-side parser is a pure regex match wrapped in ``async``
# only because every other service entry point on that module is async
# — ``await``-ing it here is allocation-free.


def _build_usage_json(usage_payload: dict) -> dict:
    """Shape the assistant-message usage blob for persistence.

    Matches what ``/metrics/usage`` aggregates: ``input`` / ``output`` /
    ``cost`` live at top level so a JSONB sum works without ``->`` gymnastics;
    ``provider`` / ``model`` power the per-model breakdown. Empty dict when
    nothing meaningful is present.

    M2.5.7 — ``model`` is the **served** name (client-facing) so the
    metrics breakdown stays stable across upstream swaps. The actual
    upstream id is preserved on ``upstream_model`` for diagnostic
    queries.
    """
    if not usage_payload:
        return {}
    tokens = usage_payload.get("tokens") or {}
    inp = int(tokens.get("input") or 0)
    out = int(tokens.get("output") or 0)
    cost = float(usage_payload.get("cost") or 0.0)
    if inp == 0 and out == 0 and cost == 0.0:
        return {}
    served = usage_payload.get("served_model") or usage_payload.get("model")
    upstream = usage_payload.get("upstream_model") or usage_payload.get("model")
    out_blob: dict = {
        "input": inp,
        "output": out,
        "cost": cost,
        "cost_currency": usage_payload.get("cost_currency") or "USD",
        "cost_matched_model": usage_payload.get("cost_matched_model"),
        "latency_ms": int(usage_payload.get("latency_ms") or 0),
        "provider": usage_payload.get("provider"),
        "model": served,
    }
    if upstream and upstream != served:
        out_blob["upstream_model"] = upstream
    return out_blob


# ─── REST ────────────────────────────────────────────────
# IMPORTANT: register *static* paths BEFORE parametric ones (`/{session_id}`)
# so FastAPI's path-matching doesn't try to coerce e.g. ``shared-with-me``
# into a UUID and 422 the request.
@router.get("/shared-with-me", response_model=list[SessionRead])
async def list_sessions_shared_with_me(
    db: DBSession,
    identity_id: CurrentIdentityId,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionRead]:
    """Sessions another user has shared with the caller (no workspace scope)."""
    rows, _total = await share_svc.list_shared_with_me(
        db, identity_id=identity_id, offset=offset, limit=limit
    )
    return [SessionRead.model_validate(r) for r in rows]


@router.get("/shared/{token}", response_model=PublicSharedSession)
async def public_shared_session(
    token: str,
    db: DBSession,
) -> PublicSharedSession:
    """Read-only public access via share token. **No authentication required.**"""
    share, sess, msgs = await share_svc.get_by_token(db, token=token)
    return PublicSharedSession(
        session_id=sess.id,
        title=sess.title,
        permission=share.permission,
        expires_at=share.expires_at,
        messages=[
            PublicSessionMessage(
                role=m.role.value if hasattr(m.role, "value") else str(m.role),
                content_json=m.content_json or {},
                tool_call_json=m.tool_call_json,
                attachments_json=m.attachments_json or [],
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.get("", response_model=list[SessionRead])
async def list_my_sessions(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await SessionRepository(db).list_for_identity(
        workspace_id=ws_id, identity_id=identity_id, offset=offset, limit=limit
    )
    return [SessionRead.model_validate(r) for r in rows]


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    s = await svc.create_session(
        db,
        workspace_id=ws_id,
        owner_identity_id=identity_id,
        kind=body.kind,
        subject_id=body.subject_id,
        title=body.title,
    )
    await db.commit()
    return SessionRead.model_validate(s)


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    s = await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    return SessionRead.model_validate(s)


@router.patch("/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    s = await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    # Manual rename → lock the title against the AI auto-title task. Only set
    # ``title_source`` when the patch actually touches ``title`` so a state
    # toggle doesn't flip the source.
    if "title" in patch:
        from app.db.models.session import TitleSource

        patch["title_source"] = TitleSource.USER
    updated = await SessionRepository(db).update(s, **patch)
    await db.commit()
    return SessionRead.model_validate(updated)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    s = await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    await SessionRepository(db).soft_delete(s)
    await db.commit()


@router.post("/{session_id}/star", response_model=StarSessionOut)
async def star_session(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    pinned: bool = Query(False),
) -> StarSessionOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    starred, pinned_state = await svc.star_session(
        db,
        identity_id=identity_id,
        session_id=session_id,
        workspace_id=ws_id,
        pinned=pinned,
    )
    await db.commit()
    return StarSessionOut(session_id=session_id, starred=starred, pinned=pinned_state)


@router.delete("/{session_id}/star", status_code=status.HTTP_204_NO_CONTENT)
async def unstar_session(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    await svc.unstar_session(db, identity_id=identity_id, session_id=session_id)
    await db.commit()


@router.get("/{session_id}/messages", response_model=list[MessageRead])
async def list_messages(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
) -> list[MessageRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await MessageRepository(db).list_for_session(
        session_id=session_id, offset=offset, limit=limit
    )
    return [MessageRead.model_validate(r) for r in rows]


@router.post(
    "/{session_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        # Per-identity LLM throughput cap. Mirrors the per-IP webhook bucket;
        # this one keys off identity_id when authenticated (see
        # ``_client_identifier`` in core.rate_limit) so a noisy tenant can't
        # starve other workspaces sharing the same backend.
        Depends(rate_limit("session_messages", limit=120, period_seconds=60))
    ],
)
async def append_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MessageRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    s = await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    msg = await svc.append_message(
        db,
        session_obj=s,
        role=body.role,
        content_json=body.content_json,
        author_identity_id=identity_id if body.role == MessageRole.USER else None,
        attachments_json=body.attachments_json,
    )
    await db.commit()
    return MessageRead.model_validate(msg)


# ─── Message rating (thumbs-up / thumbs-down) ──────────────
@router.post(
    "/{session_id}/messages/{message_id}/rate",
    response_model=MessageRatingRead,
    status_code=status.HTTP_200_OK,
)
async def rate_message(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageRatingCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MessageRatingRead:
    """Upsert the caller's thumbs-up / thumbs-down rating on an assistant message."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rating = await rating_svc.rate_message(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        message_id=message_id,
        identity_id=identity_id,
        rating=int(body.rating),
        comment=body.comment,
    )
    await db.commit()
    return MessageRatingRead.model_validate(rating)


@router.delete(
    "/{session_id}/messages/{message_id}/rate",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_message_rating(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await rating_svc.remove_rating(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        message_id=message_id,
        identity_id=identity_id,
    )
    await db.commit()


@router.get(
    "/{session_id}/ratings",
    response_model=list[MessageRatingSummary],
)
async def list_session_ratings(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[MessageRatingSummary]:
    """Aggregated likes/dislikes per assistant message + the caller's vote.

    Returned in the same order as the messages of the session. Lets the chat
    UI fold rating badges into the transcript without N+1 round-trips.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    msgs = await MessageRepository(db).list_for_session(session_id=session_id, limit=500)
    # Only assistant messages can be rated — keep the projection tight.
    assistant_ids = [m.id for m in msgs if str(m.role) == MessageRole.ASSISTANT.value]
    summary = await rating_svc.summary_for_messages(
        db, identity_id=identity_id, message_ids=assistant_ids
    )
    return [
        MessageRatingSummary(
            message_id=mid,
            likes=summary[mid]["likes"],
            dislikes=summary[mid]["dislikes"],
            my_rating=summary[mid]["my_rating"],
        )
        for mid in assistant_ids
    ]


# ─── Session goal lock (M0.1) ─────────────────────────────
@router.post(
    "/{session_id}/goals",
    response_model=SessionGoalRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("session_goal_write", limit=20, period_seconds=60))],
)
async def lock_session_goal(
    session_id: uuid.UUID,
    body: SessionGoalCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionGoalRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await goal_svc.lock_goal(
        db,
        session_id=session_id,
        workspace_id=ws_id,
        identity_id=identity_id,
        goal_text=body.goal_text,
        success_criteria=body.success_criteria,
        alignment_threshold=body.alignment_threshold,
        metadata_json=body.metadata_json,
    )
    await db.commit()
    return SessionGoalRead.model_validate(row)


@router.patch(
    "/{session_id}/goals/{goal_id}",
    response_model=SessionGoalRead,
    dependencies=[Depends(rate_limit("session_goal_write", limit=20, period_seconds=60))],
)
async def update_session_goal(
    session_id: uuid.UUID,
    goal_id: uuid.UUID,
    body: SessionGoalUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionGoalRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    # Cross-validate the goal really belongs to this session.
    existing = await goal_svc.get_goal_or_404(db, goal_id=goal_id, workspace_id=ws_id)
    if existing.session_id != session_id:
        from app.core.errors import NotFound

        raise NotFound("goal not found", code="session_goal.not_found")
    row = await goal_svc.update_goal(
        db,
        goal_id=goal_id,
        workspace_id=ws_id,
        actor_identity_id=identity_id,
        goal_text=body.goal_text,
        success_criteria=body.success_criteria,
        alignment_threshold=body.alignment_threshold,
        metadata_json=body.metadata_json,
    )
    await db.commit()
    await db.refresh(row)
    return SessionGoalRead.model_validate(row)


@router.post(
    "/{session_id}/goals/{goal_id}/unlock",
    response_model=SessionGoalRead,
    dependencies=[Depends(rate_limit("session_goal_write", limit=20, period_seconds=60))],
)
async def unlock_session_goal(
    session_id: uuid.UUID,
    goal_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionGoalRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    existing = await goal_svc.get_goal_or_404(db, goal_id=goal_id, workspace_id=ws_id)
    if existing.session_id != session_id:
        from app.core.errors import NotFound

        raise NotFound("goal not found", code="session_goal.not_found")
    row = await goal_svc.unlock_goal(
        db,
        goal_id=goal_id,
        workspace_id=ws_id,
        actor_identity_id=identity_id,
    )
    await db.commit()
    await db.refresh(row)
    return SessionGoalRead.model_validate(row)


@router.get(
    "/{session_id}/goals",
    response_model=list[SessionGoalRead],
    dependencies=[Depends(rate_limit("session_goal_read", limit=120, period_seconds=60))],
)
async def list_session_goals(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    only_active: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionGoalRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await goal_svc.list_goals(
        db,
        session_id=session_id,
        workspace_id=ws_id,
        offset=offset,
        limit=limit,
        include_unlocked=not only_active,
    )
    return [SessionGoalRead.model_validate(r) for r in rows]


@router.get(
    "/{session_id}/alignment",
    response_model=list[GoalAlignmentScoreRead],
    dependencies=[Depends(rate_limit("session_goal_read", limit=120, period_seconds=60))],
)
async def list_session_alignment(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
) -> list[GoalAlignmentScoreRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await goal_svc.list_alignment_scores(
        db,
        session_id=session_id,
        workspace_id=ws_id,
        offset=offset,
        limit=limit,
    )
    return [GoalAlignmentScoreRead.model_validate(r) for r in rows]


@router.post(
    "/{session_id}/messages/{message_id}/realign",
    response_model=GoalAlignmentScoreRead | None,
    dependencies=[Depends(rate_limit("session_goal_realign", limit=10, period_seconds=60))],
)
async def realign_message(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> GoalAlignmentScoreRead | None:
    """Re-enqueue alignment scoring for a single assistant message.

    Returns the *previous* score row immediately; the new row arrives
    asynchronously via the ARQ worker — UI polls ``/alignment`` to pick
    it up. Returns ``null`` when there's no active goal on the session.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    active = await goal_svc.get_active_goal(db, session_id=session_id, workspace_id=ws_id)
    if active is None:
        return None
    msg = await MessageRepository(db).get(message_id)
    if msg is None or msg.workspace_id != ws_id or msg.session_id != session_id:
        from app.core.errors import NotFound

        raise NotFound("message not found", code="message.not_found")

    from app.repositories.session_goal import GoalAlignmentScoreRepository
    from app.worker.queue import enqueue

    previous = await GoalAlignmentScoreRepository(db).get_for_message(
        session_goal_id=active.id,
        message_id=message_id,
        workspace_id=ws_id,
    )
    await enqueue(
        "score_message_alignment",
        str(active.id),
        str(message_id),
    )
    return GoalAlignmentScoreRead.model_validate(previous) if previous else None


# ─── Sharing (per-session CRUD) ───────────────────────────
@router.post(
    "/{session_id}/shares",
    response_model=SessionShareRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    session_id: uuid.UUID,
    body: SessionShareCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionShareRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await share_svc.share_session(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        actor_identity_id=identity_id,
        shared_with=body.shared_with,
        generate_link=body.generate_link,
        permission=body.permission,
        visibility=body.visibility,
        expires_at=body.expires_at,
    )
    await db.commit()
    return _share_to_read(row, emails={})


@router.get("/{session_id}/shares", response_model=SessionShareList)
async def list_session_shares(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionShareList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows, emails = await share_svc.list_shares(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        actor_identity_id=identity_id,
    )
    items = [_share_to_read(row, emails=emails) for row in rows]
    return SessionShareList(items=items, total=len(items))


@router.delete(
    "/{session_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session_share(
    session_id: uuid.UUID,
    share_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await share_svc.revoke_share(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        share_id=share_id,
        actor_identity_id=identity_id,
    )
    await db.commit()


def _share_to_read(row, *, emails: dict[uuid.UUID, str]) -> SessionShareRead:
    """Project a `SessionShare` ORM row → `SessionShareRead`, folding emails.

    ``emails`` is a precomputed lookup table from
    ``share_svc.list_shares`` that maps identity_id → email. For the create
    endpoint we don't have it (single-row response), so the caller passes
    ``{}`` and we leave the email fields as ``None`` — the frontend can
    refetch the list to populate them.
    """
    base = SessionShareRead.model_validate(row)
    if row.shared_with_identity_id is not None:
        base.shared_with_email = emails.get(row.shared_with_identity_id)
    if row.shared_by_identity_id is not None:
        base.shared_by_email = emails.get(row.shared_by_identity_id)
    return base


# Keep import live in case future code references the repository directly.
_ = MessageRatingRepository


# ─── Follow-up suggestions ──────────────────────────────
@router.post(
    "/{session_id}/suggestions",
    response_model=list[str],
)
async def list_session_suggestions(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[str]:
    """Generate 3-5 follow-up question suggestions for the chat composer.

    Returns ``[]`` when the bound agent has not opted in via
    ``metadata_json.chat_features.suggestions_enabled``. The flag
    defaults off so brand-new agents don't burn tokens on synthetic
    follow-ups before the operator decides whether they want them.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    session_obj = await svc.get_session_or_404(db, session_id, workspace_id=ws_id)

    agent_id = session_obj.subject_id
    if agent_id is not None:
        agent = await AgentRepository(db).get(agent_id)
        if agent is not None:
            features = (agent.metadata_json or {}).get("chat_features") or {}
            if not bool(features.get("suggestions_enabled", False)):
                return []

    from app.services.session_suggestions import generate_suggestions

    return await generate_suggestions(
        workspace_id=ws_id,
        session_id=session_id,
        agent_id=agent_id,
    )


# ─── Checkpoint / rewind ─────────────────────────────────
@router.post(
    "/{session_id}/checkpoints/{checkpoint_id}/rewind",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def rewind_session(
    session_id: uuid.UUID,
    checkpoint_id: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SessionRead:
    """Fork the conversation at ``checkpoint_id`` (a tag stamped on every
    assistant message's ``metadata_json.checkpoint_id``).

    Returns the *new* session. The original session is left intact with a
    ``forks`` audit entry pointing at the new session.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    new_session = await svc.rewind_to_checkpoint(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        checkpoint_id=checkpoint_id,
        actor_identity_id=identity_id,
    )
    await db.commit()
    return SessionRead.model_validate(new_session)


# ─── WebSocket (streaming scaffold) ──────────────────────
@router.websocket("/ws/{session_id}")
async def session_ws(websocket: WebSocket, session_id: uuid.UUID) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return

    try:
        payload = decode_token(token, expected_kind="access")
        identity_id = uuid.UUID(payload["sub"])
        workspace_id = uuid.UUID(payload["ws"]) if payload.get("ws") else None
    except Exception:
        await websocket.close(code=4401)
        return

    if workspace_id is None:
        await websocket.close(code=4403)
        return

    factory = get_session_factory()
    async with factory() as db:
        try:
            await ws_svc.ensure_member_access(
                db, workspace_id=workspace_id, identity_id=identity_id
            )
            await svc.get_session_or_404(db, session_id, workspace_id=workspace_id)
        except Exception:
            await websocket.close(code=4404)
            return

    await websocket.accept()

    # M2.5.2 — surface inflight_runs that were marked LOST by either the
    # FastAPI lifespan recovery sweep (backend restart) or the 5-minute
    # cron (heartbeat timeout). The frontend treats a non-empty
    # ``lost_runs`` system frame as a one-time prompt to offer ``/retry``.
    try:
        lost_factory = get_session_factory()
        async with lost_factory() as _lost_db:
            lost_runs = await inflight_svc.list_lost_for_session(
                _lost_db,
                session_id=session_id,
                workspace_id=workspace_id,
            )
        if lost_runs:
            await websocket.send_json(
                {
                    "type": "system",
                    "data": {
                        "kind": "lost_runs",
                        "count": len(lost_runs),
                        "run_ids": [str(r.run_id) for r in lost_runs],
                        "session_id": str(session_id),
                        "message": (
                            "Previous run(s) were interrupted by a server "
                            "restart or stalled connection. Reply with "
                            "/retry to resume or continue with a new request."
                        ),
                    },
                }
            )
    except Exception:  # pragma: no cover - never block WS accept on lookup
        log.exception("inflight lost-runs handshake failed for session %s", session_id)

    # Subscribe to approval events for this session so the HITL pump can wake
    # up whenever a tool call asks for approval. A background task drains the
    # queue and serializes `approval_request` frames onto the WebSocket.
    approval_queue = await APPROVAL_MANAGER.subscribe_session(session_id)
    # Per-connection state (seq counter + replay cache + send lock + active
    # run id). Shared by the receive loop, the per-turn task, the approval
    # pump, and the AI-title broadcaster so every outbound frame is stamped
    # with the same monotonic ``seq``.
    ws_state = _new_ws_state()
    pump_task = asyncio.create_task(
        _pump_approval_requests(websocket, approval_queue, ws_state=ws_state)
    )
    turn_task: asyncio.Task[None] | None = None
    # Mutable container the inner ``_handle_user_turn`` writes to so the
    # ``cancel`` frame handler can request a cooperative kernel-level cancel
    # (NativeBackend keeps a registry of in-flight ``run_id`` → task).
    active_run_box: dict[str, uuid.UUID | None] = {"run_id": None}

    try:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type")
            data = frame.get("data") or {}

            if kind == "user_message":
                text = str(data.get("text", ""))
                # Clients may pass attachment IDs uploaded via
                # POST /attachments. We validate them here + forward as-is to
                # the turn handler, which persists them on the user Message
                # and inlines image data into the agent request.
                raw_ids = data.get("attachment_ids") or []
                attachment_ids: list[uuid.UUID] = []
                if isinstance(raw_ids, list):
                    for x in raw_ids:
                        try:
                            attachment_ids.append(uuid.UUID(str(x)))
                        except (ValueError, TypeError):
                            continue
                # Optional ``mode`` selector from the composer (flash/thinking/plan/...).
                # Treated as a hint that we splice into ``policy`` so capabilities
                # like plan-mode can flip on for this turn only.
                mode = str(data.get("mode") or "").lower() or None
                # Optional per-turn ``model`` override (``provider:model``). The
                # ChatInput's ModelSelector forwards it for one-off picks; if
                # absent we fall back to the user's saved preference for this
                # agent (Identity.profile_json.chat_model_prefs).
                model_override = data.get("model")
                if not isinstance(model_override, str) or not model_override.strip():
                    model_override = None
                # Kick the turn into the background so we can keep reading the
                # socket (needed to handle approval_decision frames arriving
                # while the run is blocked on HITL).
                if turn_task is not None and not turn_task.done():
                    await _emit(
                        websocket,
                        ws_state,
                        {
                            "type": "error",
                            "data": {
                                "code": "session.turn_busy",
                                "message": "Previous turn still running; wait for it to finish.",
                                "retryable": True,
                            },
                        },
                    )
                    continue
                turn_task = asyncio.create_task(
                    _handle_user_turn(
                        websocket,
                        session_id=session_id,
                        workspace_id=workspace_id,
                        identity_id=identity_id,
                        text=text,
                        attachment_ids=attachment_ids,
                        active_run_box=active_run_box,
                        mode=mode,
                        model_override=model_override,
                        ws_state=ws_state,
                    )
                )

            elif kind == "resume":
                # Client reconnect handshake — replay any cached events the
                # client missed during the disconnect window. ``last_seen_seq``
                # is per-session monotonic; the cache is per-process so this
                # is best-effort (no replay across pod restarts in v1).
                last_seen_raw = data.get("last_seen_seq")
                run_id_raw = data.get("run_id")
                last_seen: int | None = None
                if isinstance(last_seen_raw, int):
                    last_seen = last_seen_raw
                elif isinstance(last_seen_raw, str) and last_seen_raw.isdigit():
                    last_seen = int(last_seen_raw)
                target_run: uuid.UUID | None = None
                if isinstance(run_id_raw, str) and run_id_raw:
                    try:
                        target_run = uuid.UUID(run_id_raw)
                    except ValueError:
                        target_run = None
                # Replay is a best-effort transient frame — bypass the cache
                # so we don't pollute it with replays of replays. Resume_ack
                # is also transient (idempotent acknowledgement).
                replayed = await _replay_cached_events(
                    websocket,
                    ws_state=ws_state,
                    last_seen_seq=last_seen,
                    run_id=target_run,
                )
                await websocket.send_json(
                    {
                        "type": "resume_ack",
                        "data": {
                            "last_seen_seq": last_seen,
                            "replayed": replayed,
                            "current_seq": ws_state["seq"],
                        },
                    }
                )

            elif kind == "approval_decision":
                await _apply_approval_decision(
                    websocket,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                    data=data,
                    ws_state=ws_state,
                )

            elif kind == "ping":
                # Keepalive frame — no seq, no caching (the client just wants
                # to know the server is alive).
                await websocket.send_json({"type": "pong"})

            elif kind == "cancel":
                # 1) Stop the in-flight turn so no new tool calls fire.
                #    First ask the kernel to cooperatively unwind the active
                #    run (closes ``agent.iter`` cleanly), then cancel the WS
                #    pump task so we stop forwarding stale frames.
                active_run_id = active_run_box.get("run_id")
                if active_run_id is not None:
                    try:
                        from app.agents.kernels.registry import get_backend

                        active_backend = get_backend("native")
                        if active_backend is not None:
                            await active_backend.cancel(active_run_id)
                    except Exception:  # pragma: no cover
                        log.exception("kernel cancel failed")
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()

                # Inform the streaming client that the turn is over so the
                # ``ReadableStream`` controller closes and the composer's
                # "Stop generating" button reverts to "Send". The turn
                # task's ``CancelledError`` branch can't safely emit from
                # inside its own cancelled context, so we do it here from
                # the still-alive receive loop.
                if active_run_id is not None:
                    await _emit(
                        websocket,
                        ws_state,
                        {
                            "type": "final",
                            "data": {
                                "run_id": str(active_run_id),
                                "reason": "cancelled",
                            },
                        },
                    )

                # 2) Parse optional ``run_id`` filter. If absent we cancel
                #    every pending approval for the session. If present we
                #    only nuke the rows belonging to that run so concurrent
                #    runs (future use) aren't collateral.
                run_id_raw = data.get("run_id")
                target_run_id: uuid.UUID | None = None
                if isinstance(run_id_raw, str) and run_id_raw:
                    try:
                        target_run_id = uuid.UUID(run_id_raw)
                    except ValueError:
                        target_run_id = None

                cancelled_rows = await _cancel_session_approvals(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=target_run_id,
                    decided_by_identity_id=identity_id,
                    reason="session cancelled",
                )
                # 3) Signal the runner callbacks so make_approval_callback
                #    wakes up and the tool call returns False.
                for row in cancelled_rows:
                    await APPROVAL_MANAGER.decide(
                        row.id,
                        approved=False,
                        reason="session cancelled",
                        decided_by=identity_id,
                    )
                # 4) Echo the cancellations back so the UI doesn't keep the
                #    amber cards around. The pump also pushes these when the
                #    runner side fires ``approval_update`` naturally; this is
                #    a belt-and-suspenders for the case where the runner task
                #    is already dead.
                for row in cancelled_rows:
                    await _emit(
                        websocket,
                        ws_state,
                        {
                            "type": "approval_update",
                            "data": {
                                "id": str(row.id),
                                "status": "cancelled",
                                "reason": row.decided_reason,
                            },
                        },
                    )
                await websocket.send_json({"type": "pong"})

            else:
                await _emit(
                    websocket,
                    ws_state,
                    {
                        "type": "error",
                        "data": {
                            "code": "ws.unknown_frame",
                            "message": f"Unknown frame type: {kind!r}",
                            "retryable": False,
                        },
                    },
                )
    except WebSocketDisconnect:
        return
    finally:
        pump_task.cancel()
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        await APPROVAL_MANAGER.unsubscribe_session(session_id)


async def _cancel_session_approvals(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID | None,
    decided_by_identity_id: uuid.UUID,
    reason: str,
) -> list:
    """Flip every still-pending approval for this session to CANCELLED.

    Runs in its own DB session so we don't step on the WS dependency-injected
    one (which is tied to the WS request scope).
    """
    from app.db.session import get_session_factory  # local to avoid cycles

    factory = get_session_factory()
    async with factory() as db:
        repo = ApprovalRepository(db)
        rows = await repo.cancel_pending_for_session(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            decided_by_identity_id=decided_by_identity_id,
            reason=reason,
            now=utcnow_naive(),
        )
        await db.commit()
        for row in rows:
            await db.refresh(row)
        return rows


async def _pump_approval_requests(
    websocket: WebSocket, queue: asyncio.Queue, *, ws_state: _WsState
) -> None:
    """Forward ``APPROVAL_MANAGER`` events for this session to the client."""
    try:
        while True:
            entry = await queue.get()
            try:
                await _emit(
                    websocket,
                    ws_state,
                    {
                        "type": "approval_request",
                        "data": {
                            "id": str(entry.id),
                            "tool_name": entry.tool_name,
                            "tool_args": entry.tool_args,
                            "summary": entry.summary,
                            "created_at": entry.created_at.isoformat(),
                            "expires_at": entry.expires_at.isoformat(),
                            "session_id": str(entry.session_id),
                        },
                    },
                )
            except Exception:  # pragma: no cover
                log.exception("approval_request push failed")
    except asyncio.CancelledError:
        return


async def _apply_approval_decision(
    websocket: WebSocket,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    data: dict[str, Any],
    ws_state: _WsState,
) -> None:
    """Handle an inbound ``approval_decision`` frame.

    Updates the DB row + resolves the pending future on ``APPROVAL_MANAGER``
    so the parked tool call proceeds (or raises ToolBlocked).
    """
    try:
        approval_id = uuid.UUID(str(data.get("approval_id")))
    except (ValueError, TypeError):
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": "approval.invalid_id",
                    "message": "approval_decision frame missing approval_id.",
                    "retryable": False,
                },
            },
        )
        return

    action = str(data.get("action", "deny")).lower()
    approved = action == "approve"
    reason = str(data.get("reason") or "")[:500] or None

    # 1) Permission gate + persist decision.
    from app.core.errors import PermissionDenied  # local import: avoid cycle
    from app.repositories.approval import ApprovalRepository  # local import: avoid cycle
    from app.services import permissions as perm  # local import: avoid cycle

    factory = get_session_factory()
    async with factory() as db:
        # Verify the actor still belongs to this workspace + check scope.
        try:
            membership = await ws_svc.ensure_member_access(
                db, workspace_id=workspace_id, identity_id=identity_id
            )
        except PermissionDenied as e:
            await _emit(
                websocket,
                ws_state,
                {
                    "type": "error",
                    "data": {
                        "code": e.code,
                        "message": str(e),
                        "retryable": False,
                    },
                },
            )
            return

        repo = ApprovalRepository(db)
        row = await repo.get(approval_id)
        if row is None or row.workspace_id != workspace_id:
            await _emit(
                websocket,
                ws_state,
                {
                    "type": "error",
                    "data": {
                        "code": "approval.not_found",
                        "message": "Approval not found or already decided.",
                        "retryable": False,
                    },
                },
            )
            return

        try:
            await perm.require_decide_approval(db, approval=row, actor_membership=membership)
        except PermissionDenied as e:
            await _emit(
                websocket,
                ws_state,
                {
                    "type": "error",
                    "data": {
                        "code": e.code,
                        "message": str(e),
                        "retryable": False,
                    },
                },
            )
            return

        row = await repo.decide(
            approval_id=approval_id,
            workspace_id=workspace_id,
            approved=approved,
            reason=reason,
            decided_by_identity_id=identity_id,
            now=utcnow_naive(),
        )
        await db.commit()

    if row is None:
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": "approval.not_found",
                    "message": "Approval not found or already decided.",
                    "retryable": False,
                },
            },
        )
        return

    # 2) Wake the parked tool call.
    await APPROVAL_MANAGER.decide(
        approval_id,
        approved=approved,
        reason=reason,
        decided_by=identity_id,
    )

    # 3) Ack the client.
    await _emit(
        websocket,
        ws_state,
        {
            "type": "approval_update",
            "data": {
                "id": str(approval_id),
                "status": row.status if isinstance(row.status, str) else row.status.value,
            },
        },
    )


async def _handle_goal_slash_command(
    websocket: WebSocket,
    *,
    ws_state: _WsState,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    command: str,
    payload: str,
) -> None:
    """Handle ``/goal`` / ``/goal unlock`` / ``/goal <text>`` in-band.

    Pushes a single ``system`` frame back to the client describing the
    outcome and bypasses the agent loop entirely. Errors raised by the
    service layer (validation / conflict) are surfaced as ``error``
    frames using the same code namespace as the REST endpoints so the
    UI can reuse its toast lookup table.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            if command == "status":
                active = await goal_svc.get_active_goal(
                    db, session_id=session_id, workspace_id=workspace_id
                )
                if active is None:
                    body = {"action": "status", "active": None}
                else:
                    body = {
                        "action": "status",
                        "active": {
                            "id": str(active.id),
                            "goal_text": active.goal_text,
                            "alignment_threshold": active.alignment_threshold,
                            "locked_by": str(active.locked_by),
                            "locked_at": active.locked_at.isoformat(),
                        },
                    }
            elif command == "unlock":
                active = await goal_svc.get_active_goal(
                    db, session_id=session_id, workspace_id=workspace_id
                )
                if active is None:
                    body = {"action": "unlock", "ok": False, "reason": "no_active_goal"}
                else:
                    row = await goal_svc.unlock_goal(
                        db,
                        goal_id=active.id,
                        workspace_id=workspace_id,
                        actor_identity_id=identity_id,
                    )
                    await db.commit()
                    body = {
                        "action": "unlock",
                        "ok": True,
                        "goal_id": str(row.id),
                    }
            else:
                row = await goal_svc.lock_goal(
                    db,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                    goal_text=payload,
                )
                await db.commit()
                body = {
                    "action": "lock",
                    "ok": True,
                    "goal_id": str(row.id),
                    "goal_text": row.goal_text,
                    "alignment_threshold": row.alignment_threshold,
                }
    except Exception as exc:
        code = getattr(exc, "code", "session_goal.command_failed")
        detail_message = getattr(exc, "detail", None) or str(exc) or "Goal command failed"
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": code,
                    "message": detail_message,
                    "retryable": False,
                },
            },
        )
        return

    await _emit(
        websocket,
        ws_state,
        {"type": "system", "data": {"kind": "goal", **body}},
    )


async def _handle_insights_slash_command(
    websocket: WebSocket,
    *,
    ws_state: _WsState,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    requested_days: int | None,
) -> None:
    """Queue cross-session insights and confirm via a system frame.

    Bypasses the agent loop entirely so no tokens are spent on the
    meta command. The aux LLM summarisation runs out-of-band via the
    ``generate_insights`` ARQ task and the result lands as a standard
    assistant markdown message in this session — the user just sees a
    confirmation toast in the meantime via the ``insights_queued``
    system frame.
    """
    from app.services import cross_session_insights as insights_svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            result = await insights_svc.queue_insights_generation(
                db,
                workspace_id=workspace_id,
                identity_id=identity_id,
                return_session_id=session_id,
                days=requested_days,
                invocation_kind="slash_command",
            )
            await db.commit()
    except Exception as exc:
        code = getattr(exc, "code", "insights.command_failed")
        message = getattr(exc, "detail", None) or str(exc) or "Insights command failed"
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": code,
                    "message": message,
                    "retryable": code == "insights.breaker_open",
                },
            },
        )
        return

    await _emit(
        websocket,
        ws_state,
        {
            "type": "system",
            "data": {
                "kind": "insights_queued",
                "days": int(result["days"]),
                "expected_completion_seconds": int(result.get("expected_completion_seconds", 30)),
                "message": (
                    f"Generating cross-session insights over the last "
                    f"{int(result['days'])} day(s). The summary will appear "
                    "as a new assistant message shortly."
                ),
            },
        },
    )


async def _handle_user_turn(
    websocket: WebSocket,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    text: str,
    attachment_ids: list[uuid.UUID] | None = None,
    active_run_box: dict[str, uuid.UUID | None] | None = None,
    mode: str | None = None,
    model_override: str | None = None,
    ws_state: _WsState | None = None,
) -> None:
    """Single turn: persist user msg → invoke kernel → stream events → persist assistant."""
    factory = get_session_factory()
    # ``ws_state`` is required for monotonic ``seq`` stamping + replay cache;
    # we keep the parameter optional so direct callers (tests, channels) still
    # work but surface a clear error if it's missing in the WS path.
    if ws_state is None:
        ws_state = _new_ws_state()

    # Per-turn timing probes — INFO-level structured logs on stdout so
    # ops can grep ``turn.timing`` and slice the 9s-from-send-to-first-
    # token regression by stage. Stages: turn.start, user_msg_persisted,
    # model_resolved, kernel_invoked, first_delta, final_emitted.
    _t0 = time.perf_counter()

    def _mark(stage: str) -> None:
        log.info(
            "turn.timing",
            extra={
                "session_id": str(session_id),
                "stage": stage,
                "elapsed_ms": int((time.perf_counter() - _t0) * 1000),
            },
        )

    _mark("turn.start")

    # /goal slash command — runs *before* the agent loop so the LLM never
    # sees the meta command. Returns early on match. Keeps the chat
    # transcript clean: the user message is replaced by a synthetic
    # ``system`` echo (no LLM round-trip, no token cost).
    goal_cmd = _parse_goal_slash_command(text)
    if goal_cmd is not None:
        await _handle_goal_slash_command(
            websocket,
            ws_state=ws_state,
            session_id=session_id,
            workspace_id=workspace_id,
            identity_id=identity_id,
            command=goal_cmd[0],
            payload=goal_cmd[1],
        )
        await _emit(
            websocket,
            ws_state,
            {"type": "final", "data": {"reason": "slash_command", "kind": "goal"}},
        )
        return

    # /insights slash command (M4.5) — same pattern as /goal: bypass
    # the agent loop entirely so the LLM never sees the meta command.
    # The aux summarisation runs out-of-band via ARQ and lands as a
    # standalone assistant markdown message in the same session.
    from app.services import cross_session_insights as insights_svc

    insights_args = await insights_svc.parse_insights_command(text)
    if insights_args is not None:
        await _handle_insights_slash_command(
            websocket,
            ws_state=ws_state,
            session_id=session_id,
            workspace_id=workspace_id,
            identity_id=identity_id,
            requested_days=insights_args.get("days"),
        )
        await _emit(
            websocket,
            ws_state,
            {"type": "final", "data": {"reason": "slash_command", "kind": "insights"}},
        )
        return

    # Load + validate attachments (workspace-scoped). The helper:
    #   - stashes a metadata ref on the user Message (for history reload),
    #   - hands image bytes to the kernel as ``BinaryContent``,
    #   - copies non-image attachments into the session scratch dir so the
    #     filesystem tools (read_file/list_files/search_files) can see them,
    #   - extracts a small plaintext excerpt of supported docs (text/PDF)
    #     and returns it as a prompt prefix.
    attachment_refs: list[dict] = []
    attachment_blobs: list[tuple[str, str, bytes]] = []  # (kind, mime, data)
    attachment_prompt_prefix = ""
    if attachment_ids:
        from app.services import attachment as att_svc

        async with factory() as adb:
            prepared = await att_svc.prepare_for_chat_turn(
                adb,
                workspace_id=workspace_id,
                session_id=session_id,
                attachment_ids=attachment_ids,
            )
            await adb.commit()
        attachment_refs = prepared.refs
        attachment_blobs = prepared.image_blobs
        attachment_prompt_prefix = prepared.prompt_prefix

    # 1) Persist user message + resolve Agent for this session.
    async with factory() as db:
        session_obj = await svc.get_session_or_404(db, session_id, workspace_id=workspace_id)
        await svc.append_message(
            db,
            session_obj=session_obj,
            role=MessageRole.USER,
            content_json={"text": text},
            author_identity_id=identity_id,
            attachments_json=attachment_refs,
        )

        is_squad = session_obj.kind == SessionKind.SQUAD
        squad_subagents_spec: dict | None = None

        if is_squad:
            squad = (
                await SquadRepository(db).get(session_obj.subject_id)
                if session_obj.subject_id
                else None
            )
            if squad is None or squad.workspace_id != workspace_id:
                await db.commit()
                await _emit(
                    websocket,
                    ws_state,
                    {
                        "type": "error",
                        "data": {
                            "code": "session.squad_not_found",
                            "message": "Squad not found for this session.",
                            "retryable": False,
                        },
                    },
                )
                return
            members = await SquadMemberRepository(db).list_for_squad(squad.id)
            if not members:
                await db.commit()
                await _emit(
                    websocket,
                    ws_state,
                    {
                        "type": "error",
                        "data": {
                            "code": "session.squad_empty",
                            "message": "Squad has no members yet.",
                            "retryable": False,
                        },
                    },
                )
                return

            member_agents = []
            for m in members:
                a = await AgentRepository(db).get(m.agent_id)
                if a is not None and a.workspace_id == workspace_id:
                    member_agents.append((a, m.role_in_squad))

            # Build subagent specs from squad members. Name must be a unique
            # identifier — we fall back to role_in_squad or an id-prefix for
            # Chinese / emoji / space names.
            specs = []
            used_names: set[str] = set()
            for a, role in member_agents:
                base = _slugify(a.name) or _slugify(role) or f"agent_{str(a.id)[:8]}"
                name = base
                counter = 1
                while name in used_names:
                    counter += 1
                    name = f"{base}_{counter}"
                used_names.add(name)
                # Keep original name visible in description so LLM can map back.
                desc = a.description or a.name
                instructions = (a.persona_md or a.description or a.name).strip()[:2000]
                if a.name and a.name != name:
                    desc = f"[{a.name}] {desc}"
                specs.append({"name": name, "description": desc, "instructions": instructions})
            squad_subagents_spec = {
                "enabled": True,
                "include_general_purpose": False,
                "specs": specs,
            }
            agent_id = member_agents[0][0].id
        else:
            agent_id = session_obj.subject_id
            if agent_id is None:
                # Fall back to first workspace agent (common single-agent default).
                first_agents = await AgentRepository(db).list_visible(
                    workspace_id=workspace_id, identity_id=identity_id, limit=1
                )
                if not first_agents:
                    # Self-heal: an agentless workspace should never block a
                    # chat. Plant the canonical default on the fly (same row
                    # that ``create_workspace`` / alembic 0026 would have
                    # produced) so the user keeps moving instead of hitting
                    # ``session.no_agent``. Idempotent vs. concurrent runs
                    # because ``ensure_default_agent`` re-checks by name.
                    from app.services.agent import ensure_default_agent

                    bootstrap = await ensure_default_agent(
                        db,
                        workspace_id=workspace_id,
                        created_by=identity_id,
                    )
                    agent_id = bootstrap.id
                else:
                    agent_id = first_agents[0].id
                session_obj.subject_id = agent_id

        agent = await AgentRepository(db).get(agent_id)
        if agent is None:
            await db.commit()
            await _emit(
                websocket,
                ws_state,
                {
                    "type": "error",
                    "data": {
                        "code": "agent.not_found",
                        "message": "Agent not found",
                        "retryable": False,
                    },
                },
            )
            return

        # Load recent message history (newest 40) for context.
        recent = await MessageRepository(db).list_for_session(session_id=session_id, limit=40)
        history = [
            {
                "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                "content_json": m.content_json,
            }
            for m in recent
            if m.role in {MessageRole.USER, MessageRole.ASSISTANT}
            # Drop synthetic placeholder turns left over from older runs
            # (e.g. ``model_build_failed:*`` rows persisted before we marked
            # them ``placeholder``). Keeping them in history poisons the next
            # prompt — the model sees long ``[占位 …]`` tails with no real
            # answer and tends to either echo them or return an empty stream.
            and not (isinstance(m.content_json, dict) and m.content_json.get("placeholder") is True)
            and not (
                isinstance(m.content_json, dict)
                and isinstance(m.content_json.get("text"), str)
                and "[SenHarness · Phase 1 占位" in m.content_json["text"]
            )
        ]
        # Pop the last user message (the one we just inserted) so we don't double-submit.
        if history and history[-1]["role"] == "user":
            history.pop()

        backend_kind = str(agent.backend_kind)
        autonomy_level = (
            agent.autonomy_level.value
            if hasattr(agent.autonomy_level, "value")
            else str(agent.autonomy_level)
        )
        agent_snapshot = {
            "id": agent.id,
            "name": agent.name,
            "persona_md": agent.persona_md,
            "backend_kind": backend_kind,
            "backend_adapter_id": agent.backend_adapter_id,
            "autonomy_level": autonomy_level,
            "metadata_json": dict(agent.metadata_json or {}),
            "default_search_provider_kind": agent.default_search_provider_kind,
        }
        if is_squad:
            agent_snapshot["persona_md"] = (
                "你是 Squad 的调度主管。面对用户请求时，优先使用 `task` 工具把合适的子任务派发给"
                "最合适的成员子代理（每个子代理都是独立专精的 Agent），等结果回来后整合答复。"
                "简单问候或澄清性问题可以自己回答。"
            )
            agent_snapshot["metadata_json"] = {
                **agent_snapshot["metadata_json"],
                "subagents": squad_subagents_spec,
            }
        await db.commit()
    _mark("user_msg_persisted")

    # 2) Dispatch to the appropriate backend.
    backend = get_backend(backend_kind)
    if backend is None:
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": "kernel.backend_missing",
                    "message": f"No runtime registered for backend_kind={backend_kind!r}",
                    "retryable": False,
                },
            },
        )
        return

    # ── Resolve model override
    # Precedence: per-turn ``model`` from the composer wins; otherwise we
    # look up the user's saved preference for this agent in
    # ``Identity.profile_json.chat_model_prefs[<agent_id>]``. The string is
    # ``"provider:model"`` (e.g. ``"deepseek:deepseek-chat"``) — same shape
    # ``RunRequest.model_override`` already accepts.
    resolved_override = model_override
    if not resolved_override:
        try:
            from app.services import session_user_prefs as user_prefs

            resolved_override = await user_prefs.get_model_pref(
                workspace_id=workspace_id,
                identity_id=identity_id,
                agent_id=agent_snapshot["id"],
            )
        except Exception:  # pragma: no cover
            log.exception("user model pref lookup failed")
            resolved_override = None
    _mark("model_resolved")

    # Map attachment blobs to the RunRequest.attachments shape. We keep the
    # raw bytes here because they're about to be inlined into the pydantic-ai
    # call; text-only kernels can ignore this list.
    attachments_for_req: list[dict] = []
    for kind, mime, data in attachment_blobs:
        attachments_for_req.append({"kind": kind, "mime_type": mime, "data": data})

    # Prepend the attachment block (if any) so the model sees document
    # excerpts + the list of files now sitting in scratch. The original
    # ``text`` is what the user actually typed and is what we persist on
    # the user Message — the prefix is for the kernel only.
    user_text_for_kernel = f"{attachment_prompt_prefix}{text}" if attachment_prompt_prefix else text

    run_id = uuid.uuid4()
    if active_run_box is not None:
        active_run_box["run_id"] = run_id
    # Stamp the active run id on the per-connection state so every emitted
    # event picks up ``data.run_id`` and reconnects can filter the replay.
    ws_state["current_run_id"] = run_id

    # ── Map composer mode → policy keys
    # ``flash`` is the default — no reasoning_effort hint.
    # ``thinking`` boosts reasoning_effort (provider-specific; harness reads
    #              ``policy.reasoning_effort`` directly).
    # ``plan``     flips ``policy.plan = True`` so the planner subagent fires.
    # ``subagent`` forces ``subagents.enabled = True`` (operator default may
    #              be off; this lets the user override per-turn).
    metadata = agent_snapshot["metadata_json"]
    base_subagents = metadata.get("subagents")
    if mode == "subagent":
        if base_subagents is None or base_subagents is True:
            subagents_policy: Any = {
                "enabled": True,
                "include_general_purpose": True,
                "specs": [],
            }
        elif isinstance(base_subagents, dict):
            subagents_policy = dict(base_subagents)
            subagents_policy["enabled"] = True
        elif isinstance(base_subagents, list):
            subagents_policy = {
                "enabled": True,
                "include_general_purpose": True,
                "specs": list(base_subagents),
            }
        else:
            # Falsy / unsupported → still flip on with a minimal default.
            subagents_policy = {
                "enabled": True,
                "include_general_purpose": True,
                "specs": [],
            }
    else:
        subagents_policy = base_subagents

    if mode == "thinking":
        reasoning_effort: str | None = "high"
    elif mode == "flash":
        reasoning_effort = None
    else:
        reasoning_effort = metadata.get("reasoning_effort")

    _first_delta_seen = False

    def _on_first_delta() -> None:
        nonlocal _first_delta_seen
        if _first_delta_seen:
            return
        _first_delta_seen = True
        _mark("first_delta")

    # Resolve the agent's per-tool allow-list. Empty list = "use the
    # runner's DEFAULT_TOOLBOX" (current behaviour); a non-empty list
    # makes the agent's tool surface an explicit allow-list driven by
    # the AbilitiesTab toggles.
    tools_meta = metadata.get("tools")
    builtin_override = tools_meta.get("builtin") if isinstance(tools_meta, dict) else None
    toolbox_override: list[str] = []
    if isinstance(builtin_override, list):
        toolbox_override = [str(t) for t in builtin_override if isinstance(t, str)]

    # Per-agent tool-round cap. Default 50 — well above pydantic-ai's
    # historical "12 iterations is plenty" budget so existing agents
    # don't lose runway; operators can dial it down for cost control.
    raw_rounds = metadata.get("max_tool_rounds")
    try:
        max_tool_rounds = int(raw_rounds) if raw_rounds is not None else 50
    except (TypeError, ValueError):
        max_tool_rounds = 50
    max_tool_rounds = max(1, min(max_tool_rounds, 500))

    req = RunRequest(
        run_id=run_id,
        workspace_id=workspace_id,
        agent_id=agent_snapshot["id"],
        session_id=session_id,
        identity_id=identity_id,
        user_text=user_text_for_kernel,
        message_history=history,
        attachments=attachments_for_req,
        toolbox=toolbox_override,
        skills=[],
        iteration_budget=max_tool_rounds,
        model_override=resolved_override,
        on_first_delta=_on_first_delta,
        policy={
            "autonomy_level": agent_snapshot["autonomy_level"],
            "backend_adapter_id": (
                str(agent_snapshot["backend_adapter_id"])
                if agent_snapshot.get("backend_adapter_id")
                else None
            ),
            "code_mode": metadata.get("code_mode"),
            "context": metadata.get("context") or {},
            "subagents": subagents_policy,
            "skills": metadata.get("skills"),
            "todos": metadata.get("todos"),
            "sandbox": metadata.get("sandbox"),
            # D3 — guards / approvals / budget propagation. Must be forwarded
            # explicitly; otherwise build_tool_guard() / build_content_guards()
            # never fire and the agent appears to "execute" tools without ever
            # gating them.
            "approvals": metadata.get("approvals"),
            "shields": metadata.get("shields"),
            "budget": metadata.get("budget"),
            "approval_ttl_seconds": metadata.get("approval_ttl_seconds"),
            # Mode flags from the frontend composer (flash / thinking / plan / subagent).
            # ``plan`` flips on the planner subagent injection in the runner.
            "plan": (mode == "plan") or bool(metadata.get("plan", False)),
            # ``reasoning_effort`` is read first by the native runner and
            # passed straight to ``model_settings`` (provider-aware: OpenAI
            # honors ``low|medium|high``; Anthropic uses ``thinking_budget``
            # via a separate path). Kept ``thinking_mode`` for backward compat
            # with anything still reading the older key.
            "reasoning_effort": reasoning_effort,
            "thinking_mode": ("high" if mode == "thinking" else metadata.get("thinking_mode")),
            "mode": mode,
            "persona_md": agent_snapshot["persona_md"],
            "default_search_provider_kind": agent_snapshot.get("default_search_provider_kind"),
            # Per-agent fallback model. When the primary fails to build
            # or stream, the runner falls back to this ``provider:model``
            # string before surfacing ``model.unavailable`` to the user.
            "fallback_model": metadata.get("fallback_model"),
            # Soft-only field this milestone; metadata-only signal for
            # future scheduler integration.
            # TODO(M2.5): runner support for ``max_concurrent_tasks``
            # alongside delegate_batch and squad fan-out.
            "max_concurrent_tasks": metadata.get("max_concurrent_tasks"),
            "workspace_id": str(workspace_id),
            "session_id": str(session_id),
        },
    )

    # 3) Stream events + persist assistant message at the end.
    full_text_parts: list[str] = []
    tool_events: list[dict] = []
    # Richer event log for the M0.2 artifact fold — tool_events alone
    # is missing kind metadata + delta/thinking/final frames.
    collected_events: list[dict] = []
    final_payload: dict = {}
    usage_payload: dict = {}
    run_exc: BaseException | None = None
    event_seq = 0

    # M2.5.2 — register the top-level run spine so a backend restart can
    # detect the orphan and notify the user. Best-effort; the helper
    # swallows its own errors so a degraded recovery path can't break
    # the chat turn.
    await _inflight_register(
        run_id=run_id,
        session_id=session_id,
        workspace_id=workspace_id,
        backend_kind=backend_kind,
        agent_id=agent_snapshot["id"] if agent_snapshot else None,
        identity_id=identity_id,
        request_snapshot=_build_inflight_snapshot(req, mode=mode),
    )

    _mark("kernel_invoked")

    try:
        async for ev in backend.run(req):
            payload = ev.to_wire()
            await _emit(websocket, ws_state, payload)
            collected_events.append({"kind": ev.kind.value, "data": dict(ev.data)})
            event_seq += 1
            # Skip per-token DELTA heartbeats — too chatty. Every
            # milestone (tool call / tool result / usage / thinking /
            # approval / final) keeps the spine row warm without
            # turning the chat turn into a write storm.
            if ev.kind != RunEventKind.DELTA:
                await _inflight_heartbeat(run_id=run_id, seq=event_seq)
            if ev.kind == RunEventKind.DELTA:
                full_text_parts.append(ev.data.get("text", ""))
            elif ev.kind in (RunEventKind.TOOL_CALL, RunEventKind.TOOL_RESULT):
                tool_events.append(ev.data)
            elif ev.kind == RunEventKind.USAGE:
                usage_payload = ev.data
            elif ev.kind == RunEventKind.FINAL:
                final_payload = ev.data
                _mark("final_emitted")
    except asyncio.CancelledError as cancel_exc:
        # User-initiated cancel: kernel already wound down via NativeBackend.cancel
        # so no further frames will arrive. Don't surface this as a kernel error.
        run_exc = cancel_exc
        if active_run_box is not None:
            active_run_box["run_id"] = None
        ws_state["current_run_id"] = None
        await _inflight_finish(
            run_id=run_id,
            state=inflight_svc.InflightRunState.CANCELLED,
            reason="ws cancel",
        )
        # Capture the partial / cancelled artifact before re-raising so the
        # cancellation event still leaves a row for Curator inspection.
        # Cancelled artifacts intentionally skip the M0.3 judge (token saver).
        await _capture_run_artifact(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_snapshot["id"] if agent_snapshot else None,
            identity_id=identity_id,
            user_text=text,
            events=collected_events,
            raised_exc=cancel_exc,
            backend=backend,
        )
        raise
    except Exception as e:  # pragma: no cover
        log.exception("kernel run failed")
        run_exc = e
        await _inflight_finish(
            run_id=run_id,
            state=inflight_svc.InflightRunState.FAILED,
            reason="kernel run raised",
            error_kind=type(e).__name__[:80],
        )
        await _emit(
            websocket,
            ws_state,
            {
                "type": "error",
                "data": {
                    "code": "kernel.run_exception",
                    "message": str(e),
                    "retryable": True,
                },
            },
        )
    else:
        # Loop completed without raising — mark the spine row COMPLETED
        # so the WS reconnect handshake doesn't surface it as LOST.
        await _inflight_finish(
            run_id=run_id,
            state=inflight_svc.InflightRunState.COMPLETED,
            reason="run finished",
        )

    if active_run_box is not None:
        active_run_box["run_id"] = None
    ws_state["current_run_id"] = None

    # 4) Persist assistant message.
    assembled = "".join(full_text_parts) or str(final_payload.get("text") or "")
    is_placeholder = bool(final_payload.get("placeholder"))
    persisted_assistant_id: uuid.UUID | None = None
    if (assembled or tool_events) and not is_placeholder:
        async with factory() as db:
            fresh = await SessionRepository(db).get(session_id)
            if fresh is not None:
                msg = await svc.append_message(
                    db,
                    session_obj=fresh,
                    role=MessageRole.ASSISTANT,
                    content_json={"text": assembled},
                    author_agent_id=agent_snapshot["id"],
                    tool_call_json={"events": tool_events} if tool_events else None,
                    token_usage_json=_build_usage_json(usage_payload),
                )
                await db.commit()
                persisted_assistant_id = msg.id

    # 4b) M0.1 — if the session has a locked goal, enqueue an aux LLM
    # alignment score for the assistant message. Deferred 2 s so the WS
    # frame ordering (FINAL → score) is intuitive when the user looks at
    # the timeline. ``enqueue`` is fail-open: a downed Redis can't break
    # the chat turn.
    if persisted_assistant_id is not None:
        async with factory() as db:
            active_goal = await goal_svc.get_active_goal(
                db, session_id=session_id, workspace_id=workspace_id
            )
        if active_goal is not None:
            try:
                from app.worker.queue import enqueue

                await enqueue(
                    "score_message_alignment",
                    str(active_goal.id),
                    str(persisted_assistant_id),
                    _defer_by=2,
                )
            except Exception:  # pragma: no cover
                log.exception("score_message_alignment enqueue failed")

    # 4c) M0.2 — fold the run into a structured artifact. Lineage points
    # the final assistant turn at the persisted message row so PRM /
    # Curator / Evolver can always resolve back to the raw transcript.
    if persisted_assistant_id is not None:
        for ev_dict in reversed(collected_events):
            if ev_dict.get("kind") == RunEventKind.FINAL.value:
                ev_dict["data"]["message_id"] = str(persisted_assistant_id)
                break
    captured_artifact = await _capture_run_artifact(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_snapshot["id"] if agent_snapshot else None,
        identity_id=identity_id,
        user_text=text,
        events=collected_events,
        raised_exc=run_exc,
        backend=backend,
    )

    # 4d) M0.3 — async run-quality judge for successful / partial runs.
    await _enqueue_judge_for_artifact(
        artifact=captured_artifact,
        workspace_id=workspace_id,
        identity_id=identity_id,
    )

    _mark("before_promote")
    # 4e) M0.7 — drain the cache-aware mutation buffer for this session
    # so the next run boots with a coherent prompt cache. Wrapped so a
    # downed memory pipeline can't break the user-facing turn.
    await _promote_pending_memories(
        workspace_id=workspace_id,
        session_id=session_id,
        identity_id=identity_id,
    )
    _mark("promote_done")

    # 5) Best-effort AI title upgrade. Fire-and-forget; if this turn was the
    # first user message, the title is currently the truncated user text and
    # we have a real assistant reply to summarise. The task self-checks
    # ``title_source`` so we don't fight a user-set title. We hold a reference
    # so the asyncio scheduler doesn't garbage-collect it mid-run.
    if assembled and not is_placeholder:
        try:
            _title_task = asyncio.create_task(
                _upgrade_title_with_llm(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    agent_id=agent_snapshot["id"],
                    websocket=websocket,
                    ws_state=ws_state,
                )
            )
            _BG_TASKS.add(_title_task)
            _title_task.add_done_callback(_BG_TASKS.discard)
        except Exception:  # pragma: no cover
            log.exception("title upgrade task spawn failed")

    _ = BackendKind  # keep import


async def _capture_run_artifact(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID,
    user_text: str,
    events: list[dict[str, Any]],
    raised_exc: BaseException | None,
    backend: Any | None = None,
) -> Any:
    """Persist a SessionArtifact for this run; fail-open on errors.

    Returns the captured ``SessionArtifact`` row (or ``None`` if the
    pipeline failed) so the WS caller can enqueue the M0.3 judge for
    successful captures without re-querying the DB. Wrapped in an
    outer try/except — the artifact pipeline must never break the
    user-facing chat turn.

    ``backend`` is the kernel instance that drove the run. When the
    backend exposes ``get_injected_skill_ids(run_id)`` (NativeBackend
    does, OpenClaw and remote adapters typically do not) we pull the
    resolved pack ids out, persist them on the artifact row, and emit
    one ``record_usage_batch(event_kind=INJECTED)`` so the SkillPack
    use_count reflects every run that actually saw the pack. Telemetry
    failure must never break the capture lifecycle.
    """
    injected_pack_ids = _read_injected_skill_ids(backend, run_id)

    from app.services import session_artifact as artifact_svc

    factory = get_session_factory()
    captured: Any = None
    try:
        async with factory() as db:
            captured = await artifact_svc.capture_from_run_outcome(
                db,
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                identity_id=identity_id,
                user_text=user_text,
                events=events,
                raised_exc=raised_exc,
                injected_skill_pack_ids=injected_pack_ids or None,
                finished_at=datetime.now(UTC).replace(tzinfo=None),
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("session artifact capture wrapper failed for run %s", run_id)
        captured = None

    if captured is not None and injected_pack_ids:
        await _record_skill_injection_usage(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            identity_id=identity_id,
            pack_ids=injected_pack_ids,
        )
    return captured


def _read_injected_skill_ids(backend: Any | None, run_id: uuid.UUID) -> list[uuid.UUID]:
    """Pull the run's injected SkillPack id list off the backend.

    Returns an empty list when the backend does not expose the
    introspection hook (OpenClaw, remote adapters) or when the lookup
    raises — the artifact still captures, telemetry is just absent for
    that backend kind.
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
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    pack_ids: list[uuid.UUID],
) -> None:
    """Best-effort SkillUsage(INJECTED) batch write.

    Runs in its own short-lived DB session so the capture transaction
    above is already committed by the time we hit this. A failure here
    must never break the run lifecycle — the audit row is the only
    breadcrumb until the M1.4 rollup picks the rows up.
    """
    from app.db.models.skill_usage import SkillUsageEventKind
    from app.services import audit as audit_svc
    from app.services import skill_usage as skill_usage_svc

    factory = get_session_factory()
    try:
        async with factory() as db:
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
            await db.commit()
    except Exception as exc:
        log.exception("skill usage record_usage_batch failed for run %s", run_id)
        try:
            async with factory() as db:
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
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit write for skill usage recording failure also failed")


async def _enqueue_judge_for_artifact(
    *,
    artifact: Any,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    """Best-effort: enqueue the M0.3 judge for a fresh artifact.

    Cancelled artifacts skip the judge entirely (token-saving) per the
    M0.3 design. Enqueue failures are written to audit so a downed
    Redis can't silently strand the verdict pipeline.
    """
    if artifact is None:
        return
    if getattr(artifact, "final_outcome", None) == "cancelled":
        return
    try:
        from app.worker.queue import enqueue

        await enqueue("judge_session_artifact", str(artifact.id), _defer_by=5)
    except Exception:  # pragma: no cover
        log.exception("judge enqueue failed for artifact %s", artifact.id)
        try:
            from app.services import audit as audit_svc

            factory = get_session_factory()
            async with factory() as db:
                await audit_svc.record(
                    db,
                    action="judge.enqueue_failed",
                    actor_identity_id=identity_id,
                    workspace_id=workspace_id,
                    resource_type="session_artifact",
                    resource_id=artifact.id,
                    summary="judge enqueue failed",
                    metadata={"artifact_id": str(artifact.id)},
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit write for judge enqueue failure failed")


async def _promote_pending_memories(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    """Drain the M0.7 pending-memory queue for this session.

    Wrapped in an outer try / except so a downed memory pipeline cannot
    break the user-facing turn — the workspace sweep cron (registered
    in :mod:`app.worker.arq_app`) is the backstop for any session that
    skipped this hook because of an exception. Audit rows here are the
    only signal until M0.10 wires the notification surface.
    """
    from app.services import audit as audit_svc
    from app.services import pending_memory as pending_memory_svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            result = await pending_memory_svc.promote_pending_memories_for_session(
                db,
                workspace_id=workspace_id,
                session_id=session_id,
                actor_identity_id=identity_id,
            )
            if result["promoted"] or result["failed"] or result["skipped"]:
                await audit_svc.record(
                    db,
                    action="memory.promotion_completed",
                    actor_identity_id=identity_id,
                    workspace_id=workspace_id,
                    resource_type="session",
                    resource_id=session_id,
                    summary=(
                        f"promoted={result['promoted']} "
                        f"skipped={result['skipped']} "
                        f"failed={result['failed']}"
                    ),
                    metadata={
                        "session_id": str(session_id),
                        "trigger": "capture_hook",
                        **result,
                    },
                )
            await db.commit()
    except Exception as exc:
        log.exception("pending memory promote failed for session %s", session_id)
        try:
            factory2 = get_session_factory()
            async with factory2() as db:
                await audit_svc.record(
                    db,
                    action="memory.promotion_failed",
                    actor_identity_id=identity_id,
                    workspace_id=workspace_id,
                    resource_type="session",
                    resource_id=session_id,
                    summary="promote hook raised — workspace sweep will retry",
                    metadata={
                        "session_id": str(session_id),
                        "error_class": type(exc).__name__,
                        "trigger": "capture_hook",
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("audit write for promote failure also failed")


async def _upgrade_title_with_llm(
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    websocket: WebSocket,
    ws_state: _WsState,
) -> None:
    """Background task: if the session title hasn't been set by the user,
    ask the cheapest available model to generate a 3-5 word title from the
    first ~6 messages and broadcast a ``session_title_updated`` frame.

    Robust to:
    - Sessions whose ``title_source`` is already ``user`` (no-op).
    - Models being unavailable (silent skip).
    - Closed websockets (the broadcast is wrapped).
    """
    try:
        from app.services import session_title as title_svc
    except Exception:  # pragma: no cover
        return

    try:
        new_title = await title_svc.maybe_upgrade_title(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
    except Exception:  # pragma: no cover
        log.exception("title upgrade failed for session %s", session_id)
        return
    if not new_title:
        return
    try:
        await _emit(
            websocket,
            ws_state,
            {
                "type": "session_title_updated",
                "data": {"session_id": str(session_id), "title": new_title},
            },
        )
    except Exception:  # pragma: no cover
        # Socket may have closed already; nothing to do.
        return
