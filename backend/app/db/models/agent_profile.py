"""Per-agent profile (M3.4).

One row per agent (unique on ``agent_id``). Cumulative aggregation of
recent runs:

* ``strengths_json`` — toolset / skill-category / domain effectiveness
  derived from successful artifacts (judge_score >= 0).
* ``failure_modes_json`` — clusters mined from negative artifacts'
  ``JudgeVerdict.process_notes_json``. Aux LLM clustering is best
  effort; on breaker tripped the structure stays empty and the
  service emits ``agent_profile.aux_skipped``.
* ``cross_workspace_stats_json`` — platform-wide rollup the platform
  admin can read via the dedicated endpoint. Service-layer enforces
  the read gate; the column is *never* exposed on the workspace-
  scoped read route.

Retention: workspace-scoped + soft-delete so the M0.11 GDPR cascade
hits the row through the ``CASCADE_TARGETS`` whitelist; the existing
``inspect.has_table`` guard keeps the entry inert on pre-0055
deployments.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class AgentProfile(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        Index(
            "ix_agent_profiles_workspace_aggregated_at",
            "workspace_id",
            "last_aggregated_at",
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    strengths_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    failure_modes_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cross_workspace_stats_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    last_aggregated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    aggregated_run_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    sample_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
