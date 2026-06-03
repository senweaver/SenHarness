"""Repositories for channel → multi-agent routing state (P0)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.channel_link import (
    ChannelBinding,
    ChannelConversationState,
    ChannelUserLink,
)
from app.db.repository import AsyncRepository


class ChannelUserLinkRepository(AsyncRepository[ChannelUserLink]):
    model = ChannelUserLink

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChannelUserLink)

    async def get_by_channel_user(
        self, *, channel_id: uuid.UUID, external_user_id: str
    ) -> ChannelUserLink | None:
        stmt = (
            select(ChannelUserLink)
            .where(
                ChannelUserLink.channel_id == channel_id,
                ChannelUserLink.external_user_id == external_user_id,
                ChannelUserLink.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ChannelConversationStateRepository(AsyncRepository[ChannelConversationState]):
    model = ChannelConversationState

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChannelConversationState)

    async def get_by_channel_peer(
        self, *, channel_id: uuid.UUID, peer_key: str, sender_key: str = ""
    ) -> ChannelConversationState | None:
        stmt = (
            select(ChannelConversationState)
            .where(
                ChannelConversationState.channel_id == channel_id,
                ChannelConversationState.peer_key == peer_key,
                ChannelConversationState.sender_key == sender_key,
                ChannelConversationState.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ChannelBindingRepository(AsyncRepository[ChannelBinding]):
    model = ChannelBinding

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChannelBinding)

    async def list_for_channel(self, *, channel_id: uuid.UUID) -> list[ChannelBinding]:
        stmt = select(ChannelBinding).where(
            ChannelBinding.channel_id == channel_id,
            ChannelBinding.deleted_at.is_(None),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_for_channel(
        self, *, channel_id: uuid.UUID, binding_id: uuid.UUID
    ) -> ChannelBinding | None:
        stmt = (
            select(ChannelBinding)
            .where(
                ChannelBinding.id == binding_id,
                ChannelBinding.channel_id == channel_id,
                ChannelBinding.deleted_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
