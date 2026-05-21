"""Audit event log.

Captures high-value platform + workspace activity that auditors or admins may
need to review later. Approvals already have their own dedicated table
(``approvals``) for HITL tool-call history; this table is for everything else:
auth, Agent / Squad CRUD, session sharing, marketplace clones, report
decisions, etc.

Design notes:

* ``workspace_id`` is **nullable** so we can log platform-level events
  (platform admin actions, failed logins before a workspace is chosen).
* ``actor_identity_id`` is **nullable** so background/system events (scheduler
  job failures, webhook deliveries) can also land here with ``None``.
* ``resource_type`` + ``resource_id`` is a weak foreign key — we don't set FKs
  because the referenced row may be soft-deleted or hard-deleted; we still
  want the audit row to survive.
* ``metadata_json`` carries structured extras — before/after diffs, tool
  arguments, report decisions, etc. Don't put secrets here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_identity_id", "created_at"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Dot-delimited namespace: "<domain>.<verb>" (e.g. "agent.create",
    # "approval.decide"). Keep stable — the UI filters on this literal value.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # Weak resource pointer.
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
