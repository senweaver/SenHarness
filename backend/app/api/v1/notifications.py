"""Notifications API + websocket push.

The endpoints stay workspace-scoped (the in-app bell sits inside a
specific workspace context); cross-workspace personal preferences live
on the ``/me/notification-prefs`` route documented in
:mod:`app.api.v1.me`. Every public route declares an explicit rate
limit per the M0 cross-cutting checklist.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
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


@router.get(
    "",
    response_model=list[NotificationRead],
    dependencies=[Depends(rate_limit("notifications_list", limit=60, period_seconds=60))],
)
async def list_notifications(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    unread_only: bool = Query(False),
    read_only: bool = Query(
        False,
        description="When True, only rows with non-null ``read_at`` are returned.",
    ),
    limit: int = Query(50, ge=1, le=200),
    event_key: str | None = Query(
        None,
        max_length=64,
        description="Optional ``Notification.kind`` filter.",
    ),
    urgency: str | None = Query(
        None,
        max_length=16,
        description=(
            "Optional ``NotificationLevel`` filter — "
            "``info`` / ``success`` / ``warning`` / ``error``."
        ),
    ),
    q: str | None = Query(
        None,
        max_length=120,
        description="Case-insensitive substring match against title / body / kind.",
    ),
    offset: int = Query(0, ge=0, le=10000),
) -> list[NotificationRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await NotificationRepository(db).list_for_recipient(
        workspace_id=ws_id,
        recipient_identity_id=identity_id,
        unread_only=unread_only,
        limit=min(max(limit + offset, limit), 200),
    )
    if event_key:
        rows = [r for r in rows if r.kind == event_key]
    if read_only:
        rows = [r for r in rows if r.read_at is not None]
    if urgency:
        target = urgency.lower()
        rows = [r for r in rows if str(r.level).lower() == target]
    if q:
        needle = q.strip().lower()
        if needle:
            rows = [
                r
                for r in rows
                if needle in (r.title or "").lower()
                or needle in (r.body or "").lower()
                or needle in (r.kind or "").lower()
            ]
    if offset:
        rows = list(rows)[offset:]
    return [NotificationRead.model_validate(r) for r in rows]


@router.get(
    "/counts",
    dependencies=[Depends(rate_limit("notifications_count", limit=120, period_seconds=60))],
)
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


@router.get(
    "/unread-count",
    dependencies=[Depends(rate_limit("notifications_count", limit=120, period_seconds=60))],
)
async def unread_count(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    """Compact alias for ``GET /counts`` consumed by the bell badge."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    unread = await NotificationRepository(db).count_unread(
        workspace_id=ws_id,
        recipient_identity_id=identity_id,
    )
    return {"unread": unread}


@router.get(
    "/{notification_id}",
    response_model=NotificationRead,
    dependencies=[Depends(rate_limit("notifications_read", limit=120, period_seconds=60))],
)
async def get_notification(
    notification_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> NotificationRead:
    """Single-row read used by the inbox detail drawer."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await NotificationRepository(db).get(notification_id)
    if row is None or row.workspace_id != ws_id or row.recipient_identity_id != identity_id:
        raise NotFound("notification_not_found", code="notification.not_found")
    return NotificationRead.model_validate(row)


@router.post(
    "/{notification_id}/read",
    response_model=NotificationRead,
    dependencies=[Depends(rate_limit("notifications_mark", limit=60, period_seconds=60))],
)
async def mark_read(
    notification_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> NotificationRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await NotificationRepository(db).get(notification_id)
    if row is None or row.workspace_id != ws_id or row.recipient_identity_id != identity_id:
        raise NotFound("notification_not_found", code="notification.not_found")
    row = await notif_svc.mark_read(db, notification=row)
    await db.commit()
    return NotificationRead.model_validate(row)


@router.post(
    "/{notification_id}/unread",
    response_model=NotificationRead,
    dependencies=[Depends(rate_limit("notifications_mark", limit=60, period_seconds=60))],
)
async def mark_unread(
    notification_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> NotificationRead:
    """Reverse of :func:`mark_read` — clears ``read_at`` so the bell badge
    counts the row again. Used by the inbox detail drawer toggle.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    repo = NotificationRepository(db)
    row = await repo.get(notification_id)
    if row is None or row.workspace_id != ws_id or row.recipient_identity_id != identity_id:
        raise NotFound("notification_not_found", code="notification.not_found")
    if row.read_at is not None:
        row = await repo.update(row, read_at=None)
        await db.commit()
    return NotificationRead.model_validate(row)


async def _mark_all_for(db, *, workspace_id: uuid.UUID, identity_id: uuid.UUID) -> int:
    rows = await NotificationRepository(db).list_for_recipient(
        workspace_id=workspace_id,
        recipient_identity_id=identity_id,
        unread_only=True,
        limit=500,
    )
    marked = 0
    for row in rows:
        await notif_svc.mark_read(db, notification=row)
        marked += 1
    await db.commit()
    return marked


@router.post(
    "/read-all",
    dependencies=[Depends(rate_limit("notifications_bulk", limit=10, period_seconds=60))],
)
async def mark_all_read(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    marked = await _mark_all_for(db, workspace_id=ws_id, identity_id=identity_id)
    return {"marked": marked}


@router.post(
    "/mark-all-read",
    dependencies=[Depends(rate_limit("notifications_bulk", limit=10, period_seconds=60))],
)
async def mark_all_read_alias(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    """Alias for ``POST /read-all`` matching the M0.10 spec naming."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    marked = await _mark_all_for(db, workspace_id=ws_id, identity_id=identity_id)
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

    # This socket only writes (push events + keepalive ping); it never
    # reads. A send-only loop can't observe the peer — or uvicorn's
    # graceful-shutdown close — until its next write fails, which would
    # stall worker shutdown / ``--reload`` by up to the ping interval.
    # Race a ``receive()`` detector so a disconnect ends the loop at once.
    async def _wait_disconnect() -> None:
        with contextlib.suppress(Exception):
            while True:
                await websocket.receive()

    disconnect_task = asyncio.create_task(_wait_disconnect())
    try:
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {get_task, disconnect_task},
                timeout=20.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                get_task.cancel()
                break
            if get_task in done:
                await websocket.send_json(get_task.result())
            else:
                get_task.cancel()
                await websocket.send_json({"type": "ping"})
    except Exception:
        pass
    finally:
        disconnect_task.cancel()
        await notif_svc.NOTIFICATION_HUB.unsubscribe(workspace_id, identity_id, queue)
