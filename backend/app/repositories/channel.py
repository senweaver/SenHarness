"""Channel repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select

from app.db.models.channel import Channel
from app.db.repository import AsyncRepository


class ChannelRepository(AsyncRepository[Channel]):
    model = Channel

    async def list_for_workspace(
        self, *, workspace_id: uuid.UUID, limit: int = 200
    ) -> Sequence[Channel]:
        stmt = (
            select(Channel)
            .where(
                Channel.workspace_id == workspace_id,
                Channel.deleted_at.is_(None),
            )
            .order_by(desc(Channel.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_inbound_token(self, token: str) -> Channel | None:
        stmt = select(Channel).where(
            Channel.inbound_token == token,
            Channel.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
