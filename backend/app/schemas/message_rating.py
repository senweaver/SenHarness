"""DTOs for assistant-message rating (thumbs-up / thumbs-down)."""

from __future__ import annotations

import re
import uuid
from enum import IntEnum

from pydantic import Field, field_validator

from app.schemas._base import ORMModel, Timestamped


class RatingValue(IntEnum):
    """Allowed feedback values."""

    LIKE = 1
    DISLIKE = -1


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_comment(comment: str | None) -> str | None:
    """Strip control chars + clamp to 2000 chars; ``None`` if empty after trim."""
    if not comment:
        return None
    cleaned = _CONTROL_CHARS.sub("", comment).strip()
    return cleaned[:2000] or None


class MessageRatingBase(ORMModel):
    rating: RatingValue = Field(..., description="1 for like, -1 for dislike.")
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional free-form feedback comment (≤ 2000 chars).",
    )

    @field_validator("comment", mode="before")
    @classmethod
    def _normalise_comment(cls, v: str | None) -> str | None:
        return _sanitize_comment(v)


class MessageRatingCreate(MessageRatingBase):
    """Body for ``POST /sessions/{sid}/messages/{mid}/rate`` (upsert)."""


class MessageRatingRead(MessageRatingBase, Timestamped):
    """Single rating row + identity / message / workspace ids."""

    workspace_id: uuid.UUID
    message_id: uuid.UUID
    identity_id: uuid.UUID


class MessageRatingSummary(ORMModel):
    """Aggregated counts for one message — drives the chat-bubble badges."""

    message_id: uuid.UUID
    likes: int = 0
    dislikes: int = 0
    my_rating: RatingValue | None = Field(
        default=None,
        description="The current caller's rating, or null if not rated.",
    )
