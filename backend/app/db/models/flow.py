"""Flow — scheduled / triggered Agent runs (cron, webhook, manual).

Conceptually: a Flow takes a *prompt template* + a *trigger* + a bound
Agent/Squad and produces a run. Each fire generates a ``FlowRun`` row plus a
new ``Session`` so the conversation can be inspected from the chat UI.

Phases:
    P0 — cron + manual triggers (this file).
    P1 — webhook trigger with payload-to-prompt templating.
    P2 — multi-step flows (call Agent A, then Agent B with A's output).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class FlowTriggerKind(StrEnum):
    CRON = "cron"
    WEBHOOK = "webhook"
    MANUAL = "manual"


class FlowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Flow(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "flows"
    __table_args__ = (
        Index("ix_flows_workspace_enabled", "workspace_id", "enabled"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    trigger_kind: Mapped[FlowTriggerKind] = mapped_column(
        String(16), default=FlowTriggerKind.MANUAL, nullable=False
    )
    # For CRON: {"expr": "0 9 * * *", "tz": "Asia/Shanghai"}
    # For WEBHOOK: {"token": "..."}
    trigger_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Exactly one of these should be set.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    squad_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("squads.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Prompt text (supports simple {{var}} substitution from trigger payload).
    # Used for "classic mode" flows (graph_json == {}).
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)

    # Visual DAG — ``{"nodes": [...], "edges": [...]}``. Non-empty = use
    # flow_engine.run_graph(); empty = fall back to prompt_template.
    graph_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class FlowRun(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "flow_runs"
    __table_args__ = (
        Index("ix_flow_runs_flow_created", "flow_id", "created_at"),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flows.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_kind: Mapped[FlowTriggerKind] = mapped_column(String(16), nullable=False)
    trigger_payload_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    status: Mapped[FlowRunStatus] = mapped_column(
        String(16), default=FlowRunStatus.PENDING, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-node trace for the visual DAG. Each entry:
    # ``{node_id, status, started_at, finished_at, input, output, error}``.
    # Empty list for legacy (non-graph) flow runs.
    node_events_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    triggered_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
