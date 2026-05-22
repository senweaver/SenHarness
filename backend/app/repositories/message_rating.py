"""Repository for `MessageRating` rows."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, select

from app.db.models.message_rating import MessageRating
from app.db.repository import AsyncRepository


class MessageRatingRepository(AsyncRepository[MessageRating]):
    model = MessageRating

    async def get_for_user(
        self, *, message_id: uuid.UUID, identity_id: uuid.UUID
    ) -> MessageRating | None:
        """Fetch the calling user's rating on a single message, if any."""
        return await self.get_by(message_id=message_id, identity_id=identity_id)

    async def aggregate(
        self, *, message_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, int]]:
        """Return ``{message_id: {likes, dislikes}}`` for the given messages.

        Skips messages with no ratings so callers can fold the result into a
        ``defaultdict(int)`` without checking presence.
        """
        if not message_ids:
            return {}
        stmt = (
            select(
                MessageRating.message_id,
                func.sum(case((MessageRating.rating == 1, 1), else_=0)).label("likes"),
                func.sum(case((MessageRating.rating == -1, 1), else_=0)).label("dislikes"),
            )
            .where(MessageRating.message_id.in_(list(message_ids)))
            .group_by(MessageRating.message_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            row.message_id: {"likes": int(row.likes or 0), "dislikes": int(row.dislikes or 0)}
            for row in rows
        }

    async def my_ratings(
        self, *, identity_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """``{message_id: rating}`` for messages the caller already rated."""
        if not message_ids:
            return {}
        stmt = select(MessageRating.message_id, MessageRating.rating).where(
            MessageRating.identity_id == identity_id,
            MessageRating.message_id.in_(list(message_ids)),
        )
        rows = (await self.session.execute(stmt)).all()
        return {row.message_id: int(row.rating) for row in rows}
