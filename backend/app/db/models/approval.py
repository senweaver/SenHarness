"""Approval — HITL gate persisted state + audit record.

The original M0.x rows recorded one tool invocation that requested human
approval. M1.4 widens the table so non-tool-call approvals (Curator
archive proposals, M2 evolver candidate publishes, M2.8 cron flows) can
ride the same persistence + audit path: ``resource_type`` + ``resource_id``
identify the target, ``tool_name`` may stay null for those rows. The
in-memory future and the run/session columns remain authoritative for the
existing tool-call flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalResourceType(StrEnum):
    """Stable values for :attr:`Approval.resource_type` (M1.4 onwards).

    The pre-existing tool-call rows leave both columns ``NULL`` (the
    ``tool_name`` + ``session_id`` path stays the source of truth);
    every new non-tool approval kind is a member of this enum. Backed
    by ``VARCHAR(40)`` instead of a Postgres ENUM so adding values
    later doesn't need an alembic op.
    """

    SKILL_PACK_ARCHIVE = "skill_pack_archive"
    SKILL_PACK_CREATE = "skill_pack_create"
    SKILL_PACK_PATCH = "skill_pack_patch"
    # M2.7 — four additional verbs beyond the M2.1 create/patch pair so
    # the SKILL.md author surface (full-document edit, deletion, file
    # add/remove inside the pack folder) all ride the same Approval
    # pipeline. Backed by ``VARCHAR(40)`` so adding values doesn't
    # require an alembic op (matches the M1.4 design comment above).
    SKILL_PACK_EDIT = "skill_pack_edit"
    SKILL_PACK_DELETE = "skill_pack_delete"
    SKILL_PACK_WRITE_FILE = "skill_pack_write_file"
    SKILL_PACK_REMOVE_FILE = "skill_pack_remove_file"
    FLOW_CREATE = "flow_create"


class Approval(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "approvals"

    # Nullable since M1.4 — Curator / Evolver / cron-flow approvals
    # (``resource_type`` set, ``tool_name`` sentinel) have no chat
    # session. The legacy HITL tool-call path still always writes a
    # real session_id; the runtime callback fails-closed if it's NULL.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # ``tool_name`` is non-null on the legacy HITL-tool path; M1.4
    # non-tool approvals (Curator / Evolver / cron flows) write
    # ``"none"`` to keep the column NOT NULL without a schema break,
    # and rely on ``resource_type`` for routing instead.
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_args: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── M1.4 wide-approval columns ────────────────────────────
    # NULL on every legacy tool-call row; populated for Curator
    # archive proposals (``skill_pack_archive``) and the M2 evolver +
    # M2.8 flow approval kinds. Indexed because the admin UI lists
    # pending approvals filtered by both columns.
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    status: Mapped[ApprovalStatus] = mapped_column(
        String(16), default=ApprovalStatus.PENDING, nullable=False
    )

    requested_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # M2.5 — flipped to True by the TTL processor when the pre-expiry
    # reminder fan-out has fired so the next sweep doesn't notify twice.
    # Composite index ``(status, expires_at)`` (alembic 0048) backs both
    # the expiring and expired sweeps as index-only seeks.
    reminder_sent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
