"""Marketplace moderation — reports filed by users against public agents.

Lightweight workflow: any authenticated user can report a public Agent
(``POST /agents/{id}/report``). A platform admin or workspace owner can
then triage the queue from ``GET /moderation/reports`` and decide:

* ``reviewed``  — acknowledged, no action
* ``dismissed`` — report was invalid
* ``removed``   — the offending Agent has been de-listed (visibility reset
                  to ``private``) or soft-deleted

The decision is permanent; create a new report if behavior recurs.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class ReportStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"
    REMOVED = "removed"


class ReportReason(StrEnum):
    SPAM = "spam"
    INAPPROPRIATE = "inappropriate"
    COPYRIGHT = "copyright"
    SECURITY = "security"
    MISINFORMATION = "misinformation"
    OTHER = "other"


class AgentReport(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "agent_reports"
    __table_args__ = (
        Index("ix_agent_reports_status", "status"),
        Index("ix_agent_reports_agent_id", "agent_id"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    reporter_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    reason: Mapped[ReportReason] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ReportStatus] = mapped_column(
        String(16), default=ReportStatus.PENDING, nullable=False
    )
    review_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
