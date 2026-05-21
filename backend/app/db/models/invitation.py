"""Workspace invitations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class Invitation(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "invitations"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(64), default="member", nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)

    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
