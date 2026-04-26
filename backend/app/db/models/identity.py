"""Global identity — single login record per human, independent of workspaces."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin


class IdentityStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PlatformRole(StrEnum):
    """Cross-workspace platform role. Distinct from per-workspace roles."""

    USER = "user"
    PLATFORM_ADMIN = "platform_admin"


class Identity(UuidPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "identities"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[IdentityStatus] = mapped_column(
        String(32), default=IdentityStatus.ACTIVE, nullable=False
    )
    platform_role: Mapped[PlatformRole] = mapped_column(
        String(32), default=PlatformRole.USER, nullable=False
    )

    # OAuth / SSO external link
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 2FA (TOTP shared secret, stored encrypted via Vault in P3)
    mfa_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Optional per-identity metadata (interests, locale, ...)
    profile_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
