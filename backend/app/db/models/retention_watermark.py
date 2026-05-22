"""Retention sweep watermarks (M0.11).

The GDPR retention sweep is *cron-driven*, not event-driven: every five
minutes the worker scans for newly soft-deleted identities and
workspaces and cascades the deletion across every table listed in
``app.services.retention.CASCADE_TARGETS``. Watermarks are how each
sweep tick remembers where the last one stopped — without them the
sweeper would either replay history forever (slow, audit-noisy) or
have to embed business logic into the soft-delete event points (which
the task brief explicitly forbids).

One row per :class:`RetentionScopeKind`; the unique constraint on
``scope_kind`` is the contract the upsert path relies on. Failure
information (``last_error``) is kept inline so an operator can spot a
stuck sweep from one ``GET /admin/retention/watermarks`` call without
trawling the audit log.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class RetentionScopeKind(StrEnum):
    """Which deletion event a watermark row tracks.

    ``IDENTITY`` — the scope is "this human's data across every
    workspace they touch". Cascades hit identity-scoped tables only.

    ``WORKSPACE`` — the scope is "everything inside this tenant".
    Cascades hit workspace-scoped tables only; identity-only tables
    (e.g. ``email_verification_tokens``) are not touched here because
    the originating identity may still be active in another tenant.
    """

    IDENTITY = "identity"
    WORKSPACE = "workspace"


class RetentionWatermark(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "retention_watermarks"
    __table_args__ = (UniqueConstraint("scope_kind", name="uq_retention_watermarks_scope"),)

    scope_kind: Mapped[RetentionScopeKind] = mapped_column(
        Enum(
            RetentionScopeKind,
            native_enum=False,
            length=20,
            name="retention_scope_kind",
        ),
        nullable=False,
        index=True,
    )

    # Sweep selects rows where ``deleted_at > last_seen_deleted_at``
    # ordered by ``deleted_at`` ASC. After successful cascade, the
    # column advances to the deleted_at of the row just processed.
    last_seen_deleted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    last_processed_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_run_rows_affected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Stable string code (not a free-form message) so the admin UI can
    # render it deterministically. Cleared after the next successful
    # tick so a one-off blip doesn't leave a stale red dot.
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
