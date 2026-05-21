"""Conversation Session — groups messages; one per logical chat."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SessionKind(StrEnum):
    P2P = "p2p"              # one user <-> one Agent
    SQUAD = "squad"          # user <-> Squad
    CHANNEL = "channel"      # inbound via IM channel


class SessionState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TitleSource(StrEnum):
    """Where the current ``Session.title`` came from.

    ``auto_truncate``  the first user message clipped to 48 chars (default).
    ``auto_ai``        a background LLM summary (cheap model).
    ``user``           the user manually renamed; AI auto-title must not
                       overwrite this row again.
    """

    AUTO_TRUNCATE = "auto_truncate"
    AUTO_AI = "auto_ai"
    USER = "user"


class Session(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "sessions"

    kind: Mapped[SessionKind] = mapped_column(
        String(16), default=SessionKind.P2P, nullable=False, index=True
    )
    # For p2p -> agent id; for squad -> squad id; for channel -> agent or squad.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title_source: Mapped[TitleSource] = mapped_column(
        String(32),
        default=TitleSource.AUTO_TRUNCATE,
        nullable=False,
    )
    state: Mapped[SessionState] = mapped_column(
        String(32), default=SessionState.ACTIVE, nullable=False
    )
    summary_md: Mapped[str | None] = mapped_column(nullable=True)

    # Driving recent/frequency ranking
    last_message_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
