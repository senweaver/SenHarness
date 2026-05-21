"""Revoked tokens (by jti)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class TokenBlacklist(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "token_blacklist"

    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
