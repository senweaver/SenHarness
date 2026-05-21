"""Service layer for assistant-message ratings.

Single-responsibility: enforce business rules (assistant-only messages,
session-membership, upsert behaviour) and translate ORM rows into Pydantic
DTOs the API layer ships back.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.db.models.message import Message, MessageRole
from app.db.models.message_rating import MessageRating
from app.repositories.message_rating import MessageRatingRepository
from app.repositories.session import MessageRepository


async def rate_message(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    identity_id: uuid.UUID,
    rating: int,
    comment: str | None,
) -> MessageRating:
    """Upsert a rating. Validates the message exists, belongs to the session,
    is in this workspace, and was authored by an assistant."""
    if rating not in (1, -1):
        raise ValidationFailed(
            "rating must be 1 (like) or -1 (dislike)",
            code="rating.invalid_value",
        )
    msg = await _get_assistant_message(
        db,
        workspace_id=workspace_id,
        session_id=session_id,
        message_id=message_id,
    )
    repo = MessageRatingRepository(db)
    existing = await repo.get_for_user(
        message_id=msg.id, identity_id=identity_id
    )
    if existing is not None:
        return await repo.update(existing, rating=rating, comment=comment)
    return await repo.create(
        workspace_id=workspace_id,
        message_id=msg.id,
        identity_id=identity_id,
        rating=rating,
        comment=comment,
    )


async def remove_rating(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> None:
    """Delete the calling user's rating for this message (no-op if absent)."""
    msg = await _get_assistant_message(
        db,
        workspace_id=workspace_id,
        session_id=session_id,
        message_id=message_id,
    )
    repo = MessageRatingRepository(db)
    existing = await repo.get_for_user(
        message_id=msg.id, identity_id=identity_id
    )
    if existing is not None:
        await repo.hard_delete(existing)


async def summary_for_messages(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    message_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    """Return ``{message_id: {likes, dislikes, my_rating}}`` for the messages.

    Folds the per-user rating + aggregate counts together so the chat page
    can render badges in a single round-trip.
    """
    repo = MessageRatingRepository(db)
    counts = await repo.aggregate(message_ids=message_ids)
    mine = await repo.my_ratings(identity_id=identity_id, message_ids=message_ids)
    out: dict[uuid.UUID, dict] = {}
    for mid in message_ids:
        cell = counts.get(mid, {"likes": 0, "dislikes": 0})
        out[mid] = {
            "likes": cell["likes"],
            "dislikes": cell["dislikes"],
            "my_rating": mine.get(mid),
        }
    return out


async def _get_assistant_message(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message:
    msg = await MessageRepository(db).get(message_id)
    if (
        msg is None
        or msg.workspace_id != workspace_id
        or msg.session_id != session_id
    ):
        raise NotFound("message_not_found", code="message.not_found")
    if msg.role != MessageRole.ASSISTANT:
        raise ValidationFailed(
            "Only assistant messages can be rated",
            code="rating.not_assistant",
        )
    return msg
