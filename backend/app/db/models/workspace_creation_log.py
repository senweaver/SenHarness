"""Workspace creation audit log (M0.12).

One row per ``POST /workspaces`` (manual UI), self-register / OAuth
auto-provision, invitation redeem, or admin provision call. The table
backs three responsibilities:

1. **Quota counting** for an identity — how many active workspaces a
   user currently owns. ``soft_deleted_workspace`` tracks whether the
   referenced workspace has since been soft-deleted; the quota count
   honours the platform setting ``count_soft_deleted`` to decide
   whether deleted workspaces still occupy a slot.
2. **Rate-window observation** — recent rows in the same identity's
   stream let the API surface ``rate_window_used`` without needing a
   separate Redis snapshot.
3. **Forensic / abuse review** — the ``creation_kind`` discriminates
   "this user signed up for an account" from "this user clicked +New
   Workspace 47 times in 5 minutes" so platform admins can
   ban-and-purge with audit cover.

Identity-only: a workspace soft-delete does *not* cascade these rows
because the issuing identity may still be active in another tenant.
The M0.11 retention sweep handles cascade for this table on identity
soft-delete via ``CASCADE_TARGETS``.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class CreationKind(StrEnum):
    """How the workspace came into existence.

    The string is used as a discriminator in audit metadata, the
    ``GET /me/workspace-quota`` payload, and the ``GET /admin/workspace
    -quotas`` rows. Adding a kind requires a frontend i18n entry plus
    a quota service decision on whether the kind counts towards the
    creator's per-source default.
    """

    SELF_REGISTER = "self_register"
    OAUTH_REGISTER = "oauth_register"
    MANUAL = "manual"
    INVITATION_REDEEM = "invitation_redeem"
    ADMIN_PROVISION = "admin_provision"


class WorkspaceCreationLog(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "workspace_creation_logs"
    __table_args__ = (
        Index(
            "ix_workspace_creation_logs_identity_created",
            "identity_id",
            "created_at",
        ),
        Index(
            "ix_workspace_creation_logs_identity_kind_created",
            "identity_id",
            "creation_kind",
            "created_at",
        ),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    creation_kind: Mapped[CreationKind] = mapped_column(
        Enum(
            CreationKind,
            native_enum=False,
            length=30,
            name="workspace_creation_kind",
        ),
        nullable=False,
        index=True,
    )

    # Free-form short label distinguishing source surface within a
    # ``creation_kind`` (e.g. "register" vs "oauth_register" vs
    # "admin_console"). Optional — kept for the abuse-review UI.
    creation_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Flips to TRUE when ``DELETE /workspaces/{id}`` runs and the
    # platform setting ``count_soft_deleted`` is False. The quota
    # counter joins on this column so a deletion frees a slot in O(1)
    # without rescanning the workspaces table.
    soft_deleted_workspace: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
