"""Share-link for a session.

Two flavours coexist on the same row:

  * **Direct share** — ``shared_with_identity_id`` is set, ``token`` is NULL.
    The recipient sees the conversation under "Shared with me".
  * **Public link** — ``token`` is set, ``shared_with_identity_id`` is NULL.
    Anyone with the URL can hit ``GET /sessions/shared/{token}``.

A single conversation can have multiple shares (different recipients, or one
public link plus several direct invites). The ``visibility`` column is kept
for backward-compat with the older P0 design (private / workspace / public)
but new code should rely on ``token`` + ``shared_with_identity_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class ShareVisibility(StrEnum):
    PRIVATE = "private"
    WORKSPACE = "workspace"
    PUBLIC = "public"


class SharePermission(StrEnum):
    """Read-only or read-write access for the recipient.

    ``EDIT`` doesn't yet allow message creation through the share — it's
    reserved so the schema can express the intent and we can flip the
    runtime enforcement on later without migration churn.
    """

    VIEW = "view"
    EDIT = "edit"


class SessionShare(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "session_shares"
    __table_args__ = (
        # A given recipient can be shared the same conversation only once.
        # NULL ``shared_with_identity_id`` (link shares) doesn't conflict
        # because Postgres treats NULLs as distinct in unique constraints.
        UniqueConstraint(
            "session_id",
            "shared_with_identity_id",
            name="uq_session_shares_session_id_shared_with_identity_id",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Public-link token. NULL when this row is a direct user share.
    token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    visibility: Mapped[ShareVisibility] = mapped_column(
        String(16), default=ShareVisibility.WORKSPACE, nullable=False
    )
    permission: Mapped[SharePermission] = mapped_column(
        String(16), default=SharePermission.VIEW, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # ``created_by`` (legacy, P0) and ``shared_by_identity_id`` are
    # synonymous for new rows — services write both. We keep ``created_by``
    # nullable for backward-compat with rows persisted before the alias.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    shared_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    shared_with_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
