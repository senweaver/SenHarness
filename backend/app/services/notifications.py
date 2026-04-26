"""Notification service + in-process websocket fan-out."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.repositories.notification import NotificationRepository


class NotificationHub:
    """In-process pub/sub keyed by (workspace_id, identity_id)."""

    def __init__(self) -> None:
        self._subs: dict[tuple[uuid.UUID, uuid.UUID], set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, workspace_id: uuid.UUID, identity_id: uuid.UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        key = (workspace_id, identity_id)
        async with self._lock:
            self._subs[key].add(q)
        return q

    async def unsubscribe(
        self, workspace_id: uuid.UUID, identity_id: uuid.UUID, queue: asyncio.Queue
    ) -> None:
        key = (workspace_id, identity_id)
        async with self._lock:
            if key in self._subs:
                self._subs[key].discard(queue)
                if not self._subs[key]:
                    self._subs.pop(key, None)

    async def publish(self, workspace_id: uuid.UUID, identity_id: uuid.UUID, payload: dict) -> None:
        key = (workspace_id, identity_id)
        async with self._lock:
            queues = list(self._subs.get(key, set()))
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop oldest then try once more.
                with suppress(Exception):
                    _ = q.get_nowait()
                with suppress(Exception):
                    q.put_nowait(payload)


NOTIFICATION_HUB = NotificationHub()


async def create_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    recipient_identity_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    kind: str,
    title: str,
    body: str | None = None,
    level: str = "info",
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    action_url: str | None = None,
    metadata_json: dict | None = None,
):
    row = await NotificationRepository(session).create(
        workspace_id=workspace_id,
        recipient_identity_id=recipient_identity_id,
        actor_identity_id=actor_identity_id,
        kind=kind,
        level=level,
        title=title,
        body=body,
        resource_type=resource_type,
        resource_id=resource_id,
        action_url=action_url,
        metadata_json=metadata_json or {},
    )
    await NOTIFICATION_HUB.publish(
        workspace_id,
        recipient_identity_id,
        {
            "type": "notification.created",
            "data": {
                "id": str(row.id),
                "kind": row.kind,
                "level": row.level,
                "title": row.title,
                "body": row.body,
                "resource_type": row.resource_type,
                "resource_id": str(row.resource_id) if row.resource_id else None,
                "action_url": row.action_url,
                "metadata_json": row.metadata_json,
                "created_at": row.created_at.isoformat(),
                "read_at": None,
            },
        },
    )
    return row


async def mark_read(
    session: AsyncSession,
    *,
    notification,
):
    if notification.read_at is None:
        notification = await NotificationRepository(session).update(
            notification, read_at=utcnow_naive()
        )
        await NOTIFICATION_HUB.publish(
            notification.workspace_id,
            notification.recipient_identity_id,
            {
                "type": "notification.read",
                "data": {
                    "id": str(notification.id),
                    "read_at": notification.read_at.isoformat() if notification.read_at else None,
                },
            },
        )
    return notification
