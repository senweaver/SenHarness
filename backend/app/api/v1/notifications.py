"""Notifications API + websocket push."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.security import decode_token
from app.db.session import get_session_factory
from app.repositories.notification import NotificationRepository
from app.schemas.notification import NotificationRead
from app.services import notifications as notif_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> list[NotificationRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await NotificationRepository(db).list_for_recipient(
        workspace_id=ws_id,
        recipient_identity_id=identity_id,
        unread_only=unread_only,
        limit=limit,
    )
    return [NotificationRead.model_validate(r) for r in rows]


@router.get("/counts")
async def notification_counts(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    unread = await NotificationRepository(db).count_unread(
        workspace_id=ws_id,
        recipient_identity_id=identity_id,
    )
    return {"unread": unread}


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(
    notification_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> NotificationRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await NotificationRepository(db).get(notification_id)
    if (
        row is None
        or row.workspace_id != ws_id
        or row.recipient_identity_id != identity_id
    ):
        raise NotFound("notification_not_found", code="notification.not_found")
    row = await notif_svc.mark_read(db, notification=row)
    await db.commit()
    return NotificationRead.model_validate(row)


@router.post("/read-all")
async def mark_all_read(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await NotificationRepository(db).list_for_recipient(
        workspace_id=ws_id,
        recipient_identity_id=identity_id,
        unread_only=True,
        limit=500,
    )
    marked = 0
    for row in rows:
        await notif_svc.mark_read(db, notification=row)
        marked += 1
    await db.commit()
    return {"marked": marked}


@router.websocket("/ws")
async def notifications_ws(websocket: WebSocket) -> None:
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

    # Validate membership before opening the stream.
    factory = get_session_factory()
    async with factory() as db:
        try:
            await ws_svc.ensure_member_access(
                db, workspace_id=workspace_id, identity_id=identity_id
            )
        except Exception:
            await websocket.close(code=4404)
            return

    await websocket.accept()
    queue = await notif_svc.NOTIFICATION_HUB.subscribe(workspace_id, identity_id)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                await websocket.send_json(payload)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except Exception:
        pass
    finally:
        await notif_svc.NOTIFICATION_HUB.unsubscribe(workspace_id, identity_id, queue)
