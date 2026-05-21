"""Notification repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, func, select

from app.db.models.notification import Notification
from app.db.repository import AsyncRepository


class NotificationRepository(AsyncRepository[Notification]):
    model = Notification

    async def list_for_recipient(
        self,
        *,
        workspace_id: uuid.UUID,
        recipient_identity_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 50,
    ) -> Sequence[Notification]:
        stmt = (
            select(Notification)
            .where(
                Notification.workspace_id == workspace_id,
                Notification.recipient_identity_id == recipient_identity_id,
                Notification.deleted_at.is_(None),
            )
            .order_by(desc(Notification.created_at))
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        return (await self.session.execute(stmt)).scalars().all()

    async def count_unread(
        self,
        *,
        workspace_id: uuid.UUID,
        recipient_identity_id: uuid.UUID,
    ) -> int:
        stmt = (
            select(func.count(Notification.id))
            .where(
                Notification.workspace_id == workspace_id,
                Notification.recipient_identity_id == recipient_identity_id,
                Notification.deleted_at.is_(None),
                Notification.read_at.is_(None),
            )
        )
        return int((await self.session.execute(stmt)).scalar() or 0)
