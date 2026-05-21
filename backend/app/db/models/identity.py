"""Global identity — single login record per human, independent of workspaces."""

from __future__ import annotations

from datetime import datetime
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

    # Per-identity workspace creation override. NULL means the quota
    # service falls back to the platform default for this identity's
    # source kind (self-register / OAuth / admin-provisioned). A
    # platform admin sets this through PATCH /admin/identities/{id}
    # /workspace-quota; the M0.12 grandfather migration also writes
    # it for legacy users whose owned-workspace count exceeds the new
    # default to avoid locking them out post-deploy.
    workspace_quota_override: Mapped[int | None] = mapped_column(nullable=True)

    # Per-identity notification preferences (M0.10). Keyed by
    # ``EVENT_REGISTRY`` event_key plus the reserved ``_global`` entry
    # for vacation-mode mute. ``requires_email=True`` events ignore
    # the per-identity opt-out — security mail is non-negotiable. See
    # :func:`app.services.notification_events._effective_channels` for
    # the merge order. Empty dict (the default) means "use registry
    # defaults for every event".
    notification_prefs_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )

    # First-run onboarding completion timestamp. NULL means the identity
    # has never completed (or skipped) the onboarding overlay. The flow
    # itself is triggered by ``?onboarding=1`` or the AvatarMenu
    # "restart onboarding" action — this column is an audit field, not
    # a UI trigger.
    onboarded_at: Mapped[datetime | None] = mapped_column(nullable=True)
