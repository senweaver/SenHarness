"""User feedback rating for assistant messages.

One ``MessageRating`` row per (message, identity) pair. Used by the chat
UI's thumbs-up / thumbs-down feedback to drive prompt / agent tuning, and
by ops dashboards to spot regressions in assistant quality.

The (message_id, user_id) pair is unique — re-rating the same message
overwrites the old value (services layer handles upsert).
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class MessageRating(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    """Thumbs-up / thumbs-down feedback on a single assistant message.

    Attributes:
        id: Unique rating identifier.
        workspace_id: Tenant scope (matches the message's workspace).
        message_id: The assistant message being rated.
        identity_id: The identity who submitted the rating.
        rating: ``1`` for like, ``-1`` for dislike. Enforced by CHECK.
        comment: Optional free-form comment (max 2000 chars on schema layer;
            stored as TEXT to be safe).
    """

    __tablename__ = "message_ratings"
    __table_args__ = (
        UniqueConstraint(
            "message_id", "identity_id", name="uq_message_ratings_message_id_identity_id"
        ),
        CheckConstraint("rating IN (1, -1)", name="ck_message_ratings_rating_value"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 or -1
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<MessageRating(id={self.id}, msg={self.message_id}, rating={self.rating})>"
