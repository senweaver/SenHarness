"""Email-verification opaque tokens.

Issued by the auth service when ``auth_require_email_verification`` is on.
The opaque token only travels to the user's mailbox; the row stores its
SHA-256 digest so a stolen DB dump cannot be replayed against the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class EmailVerificationToken(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "email_verification_tokens"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
