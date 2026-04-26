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
import uuid
from typing import Any

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
)
from app.schemas.session_share import (
    PublicSessionMessage,
    PublicSharedSession,
    SessionShareCreate,
    SessionShareList,
    SessionShareRead,
)
from app.services import message_rating as rating_svc
from app.services import session as svc
from app.services import session_share as share_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter()


def _slugify(name: str, *, fallback: str = "member") -> str:
    """Best-effort slug for subagent name (identifier-like).

    Strips non-identifier chars. If the resulting string is empty or reduces to
    the generic `fallback` (common for Chinese-only names), returns empty so the
    caller can fall back to a UUID-based identifier.
    """
    import re

    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip())
    s = s.strip("_").lower()[:48]
    if s in ("", fallback):
        return ""
    return s


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _build_usage_json(usage_payload: dict) -> dict:
    """Shape the assistant-message usage blob for persistence.

    Matches what ``/metrics/usage`` aggregates: ``input`` / ``output`` /
    ``cost`` live at top level so a JSONB sum works without ``->`` gymnastics;
    ``provider`` / ``model`` power the per-model breakdown. Empty dict when
    nothing meaningful is present.
    """
    if not usage_payload:
        return {}
    tokens = usage_payload.get("tokens") or {}
    inp = int(tokens.get("input") or 0)
    out = int(tokens.get("output") or 0)
    cost = float(usage_payload.get("cost") or 0.0)
    if inp == 0 and out == 0 and cost == 0.0:
        return {}
    return {
        "input": inp,
        "output": out,
        "cost": cost,
        "cost_currency": usage_payload.get("cost_currency") or "USD",
        "cost_matched_model": usage_payload.get("cost_matched_model"),
        "latency_ms": int(usage_payload.get("latency_ms") or 0),
        "provider": usage_payload.get("provider"),
        "model": usage_payload.get("model"),
    }


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
    updated = await SessionRepository(db).update(
        s, **body.model_dump(exclude_none=True)
    )
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
    msgs = await MessageRepository(db).list_for_session(
        session_id=session_id, limit=500
    )
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

    # Subscribe to approval events for this session so the HITL pump can wake
    # up whenever a tool call asks for approval. A background task drains the
    # queue and serializes `approval_request` frames onto the WebSocket.
    approval_queue = await APPROVAL_MANAGER.subscribe_session(session_id)
    pump_task = asyncio.create_task(
        _pump_approval_requests(websocket, approval_queue)
    )
    turn_task: asyncio.Task[None] | None = None

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
                # Kick the turn into the background so we can keep reading the
                # socket (needed to handle approval_decision frames arriving
                # while the run is blocked on HITL).
                if turn_task is not None and not turn_task.done():
                    await websocket.send_json(
                        {"type": "error", "data": {
                            "code": "session.turn_busy",
                            "message": "Previous turn still running; wait for it to finish.",
                            "retryable": True,
                        }}
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
                    )
                )

            elif kind == "approval_decision":
                await _apply_approval_decision(
                    websocket,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                    data=data,
                )

            elif kind == "ping":
                await websocket.send_json({"type": "pong"})

            elif kind == "cancel":
                # 1) Stop the in-flight turn so no new tool calls fire.
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()

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
                    await websocket.send_json(
                        {
                            "type": "approval_update",
                            "data": {
                                "id": str(row.id),
                                "status": "cancelled",
                                "reason": row.decided_reason,
                            },
                        }
                    )
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {
                            "code": "ws.unknown_frame",
                            "message": f"Unknown frame type: {kind!r}",
                            "retryable": False,
                        },
                    }
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
    websocket: WebSocket, queue: asyncio.Queue
) -> None:
    """Forward ``APPROVAL_MANAGER`` events for this session to the client."""
    try:
        while True:
            entry = await queue.get()
            try:
                await websocket.send_json(
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
                    }
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
) -> None:
    """Handle an inbound ``approval_decision`` frame.

    Updates the DB row + resolves the pending future on ``APPROVAL_MANAGER``
    so the parked tool call proceeds (or raises ToolBlocked).
    """
    try:
        approval_id = uuid.UUID(str(data.get("approval_id")))
    except (ValueError, TypeError):
        await websocket.send_json(
            {"type": "error", "data": {
                "code": "approval.invalid_id",
                "message": "approval_decision frame missing approval_id.",
                "retryable": False,
            }}
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
            await websocket.send_json(
                {"type": "error", "data": {
                    "code": e.code, "message": str(e), "retryable": False,
                }}
            )
            return

        repo = ApprovalRepository(db)
        row = await repo.get(approval_id)
        if row is None or row.workspace_id != workspace_id:
            await websocket.send_json(
                {"type": "error", "data": {
                    "code": "approval.not_found",
                    "message": "Approval not found or already decided.",
                    "retryable": False,
                }}
            )
            return

        try:
            await perm.require_decide_approval(
                db, approval=row, actor_membership=membership
            )
        except PermissionDenied as e:
            await websocket.send_json(
                {"type": "error", "data": {
                    "code": e.code, "message": str(e), "retryable": False,
                }}
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
        await websocket.send_json(
            {"type": "error", "data": {
                "code": "approval.not_found",
                "message": "Approval not found or already decided.",
                "retryable": False,
            }}
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
    await websocket.send_json(
        {"type": "approval_update", "data": {
            "id": str(approval_id),
            "status": row.status if isinstance(row.status, str) else row.status.value,
        }}
    )


async def _handle_user_turn(
    websocket: WebSocket,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    text: str,
    attachment_ids: list[uuid.UUID] | None = None,
) -> None:
    """Single turn: persist user msg → invoke kernel → stream events → persist assistant."""
    factory = get_session_factory()

    # Load + validate attachments (workspace-scoped). We stash them on the
    # user Message as JSON (for history) and hand over to the kernel as bytes
    # when the attachment is an image.
    attachment_refs: list[dict] = []
    attachment_blobs: list[tuple[str, str, bytes]] = []  # (kind, mime, data)
    if attachment_ids:
        from app.services import attachment as att_svc

        async with factory() as adb:
            for aid in attachment_ids:
                try:
                    att = await att_svc.get_for_read(
                        adb, attachment_id=aid, workspace_id=workspace_id
                    )
                except Exception:
                    continue
                # Re-bind to this session so cleanup later has the link.
                if att.session_id is None:
                    att.session_id = session_id
                    await adb.flush([att])
                attachment_refs.append(
                    {
                        "id": str(att.id),
                        "filename": att.filename,
                        "mime_type": att.mime_type,
                        "kind": att.kind,
                        "size_bytes": att.size_bytes,
                    }
                )
                if att.kind == "image":
                    try:
                        blob = att_svc.read_bytes(att)
                    except Exception:
                        blob = None
                    if blob is not None:
                        attachment_blobs.append((att.kind, att.mime_type, blob))
            await adb.commit()

    # 1) Persist user message + resolve Agent for this session.
    async with factory() as db:
        session_obj = await svc.get_session_or_404(
            db, session_id, workspace_id=workspace_id
        )
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
            squad = await SquadRepository(db).get(session_obj.subject_id) if session_obj.subject_id else None
            if squad is None or squad.workspace_id != workspace_id:
                await db.commit()
                await websocket.send_json(
                    {"type": "error", "data": {
                        "code": "session.squad_not_found",
                        "message": "Squad not found for this session.",
                        "retryable": False,
                    }}
                )
                return
            members = await SquadMemberRepository(db).list_for_squad(squad.id)
            if not members:
                await db.commit()
                await websocket.send_json(
                    {"type": "error", "data": {
                        "code": "session.squad_empty",
                        "message": "Squad has no members yet.",
                        "retryable": False,
                    }}
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
                base = (
                    _slugify(a.name)
                    or _slugify(role)
                    or f"agent_{str(a.id)[:8]}"
                )
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
                specs.append(
                    {"name": name, "description": desc, "instructions": instructions}
                )
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
            await websocket.send_json(
                {
                    "type": "error",
                    "data": {
                        "code": "agent.not_found",
                        "message": "Agent not found",
                        "retryable": False,
                    },
                }
            )
            return

        # Load recent message history (newest 40) for context.
        recent = await MessageRepository(db).list_for_session(
            session_id=session_id, limit=40
        )
        history = [
            {"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content_json": m.content_json}
            for m in recent
            if m.role in {MessageRole.USER, MessageRole.ASSISTANT}
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

    # 2) Dispatch to the appropriate backend.
    backend = get_backend(backend_kind)
    if backend is None:
        await websocket.send_json(
            {
                "type": "error",
                "data": {
                    "code": "kernel.backend_missing",
                    "message": f"No runtime registered for backend_kind={backend_kind!r}",
                    "retryable": False,
                },
            }
        )
        return

    # Map attachment blobs to the RunRequest.attachments shape. We keep the
    # raw bytes here because they're about to be inlined into the pydantic-ai
    # call; text-only kernels can ignore this list.
    attachments_for_req: list[dict] = []
    for kind, mime, data in attachment_blobs:
        attachments_for_req.append(
            {"kind": kind, "mime_type": mime, "data": data}
        )

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_id=agent_snapshot["id"],
        session_id=session_id,
        identity_id=identity_id,
        user_text=text,
        message_history=history,
        attachments=attachments_for_req,
        toolbox=[],  # P1 default toolbox resolved inside runner
        skills=[],
        policy={
            "autonomy_level": agent_snapshot["autonomy_level"],
            "backend_adapter_id": (
                str(agent_snapshot["backend_adapter_id"])
                if agent_snapshot.get("backend_adapter_id")
                else None
            ),
            "code_mode": agent_snapshot["metadata_json"].get("code_mode"),
            "context": agent_snapshot["metadata_json"].get("context") or {},
            "subagents": agent_snapshot["metadata_json"].get("subagents"),
            "skills": agent_snapshot["metadata_json"].get("skills"),
            "todos": agent_snapshot["metadata_json"].get("todos"),
            "sandbox": agent_snapshot["metadata_json"].get("sandbox"),
            # D3 — guards / approvals / budget propagation. Must be forwarded
            # explicitly; otherwise build_tool_guard() / build_content_guards()
            # never fire and the agent appears to "execute" tools without ever
            # gating them.
            "approvals": agent_snapshot["metadata_json"].get("approvals"),
            "shields": agent_snapshot["metadata_json"].get("shields"),
            "budget": agent_snapshot["metadata_json"].get("budget"),
            "approval_ttl_seconds": agent_snapshot["metadata_json"].get(
                "approval_ttl_seconds"
            ),
            "persona_md": agent_snapshot["persona_md"],
            "workspace_id": str(workspace_id),
            "session_id": str(session_id),
        },
    )

    # 3) Stream events + persist assistant message at the end.
    full_text_parts: list[str] = []
    tool_events: list[dict] = []
    final_payload: dict = {}
    usage_payload: dict = {}

    try:
        async for ev in backend.run(req):
            payload = ev.to_wire()
            await websocket.send_json(payload)
            if ev.kind == RunEventKind.DELTA:
                full_text_parts.append(ev.data.get("text", ""))
            elif ev.kind in (RunEventKind.TOOL_CALL, RunEventKind.TOOL_RESULT):
                tool_events.append(ev.data)
            elif ev.kind == RunEventKind.USAGE:
                usage_payload = ev.data
            elif ev.kind == RunEventKind.FINAL:
                final_payload = ev.data
    except Exception as e:  # pragma: no cover
        log.exception("kernel run failed")
        await websocket.send_json(
            {
                "type": "error",
                "data": {
                    "code": "kernel.run_exception",
                    "message": str(e),
                    "retryable": True,
                },
            }
        )

    # 4) Persist assistant message.
    assembled = "".join(full_text_parts) or str(final_payload.get("text") or "")
    if assembled or tool_events:
        async with factory() as db:
            fresh = await SessionRepository(db).get(session_id)
            if fresh is not None:
                await svc.append_message(
                    db,
                    session_obj=fresh,
                    role=MessageRole.ASSISTANT,
                    content_json={"text": assembled},
                    author_agent_id=agent_snapshot["id"],
                    tool_call_json={"events": tool_events} if tool_events else None,
                    token_usage_json=_build_usage_json(usage_payload),
                )
                await db.commit()

    _ = BackendKind  # keep import
