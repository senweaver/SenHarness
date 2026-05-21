"""Flow — scheduled / triggered Agent runs (cron, webhook, manual).

Conceptually: a Flow takes a *prompt template* + a *trigger* + a bound
Agent/Squad and produces a run. Each fire generates a ``FlowRun`` row plus a
new ``Session`` so the conversation can be inspected from the chat UI.

Phases:
    P0 — cron + manual triggers (this file).
    P1 — webhook trigger with payload-to-prompt templating.
    P2 — multi-step flows (call Agent A, then Agent B with A's output).

Execution modes (M0.6):
    AGENT             — default; full agent loop (one_shot or graph).
    NO_AGENT_SCRIPT   — runs a shell command in the sandbox; empty stdout
                         is silent, non-empty optionally escalates to agent.
    NO_AGENT_HTTP     — fires an HTTP probe; 2xx is silent, non-2xx
                         optionally escalates to agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text
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


class FlowExecutionMode(StrEnum):
    AGENT = "agent"
    NO_AGENT_SCRIPT = "no_agent_script"
    NO_AGENT_HTTP = "no_agent_http"


class FlowRunOutcome(StrEnum):
    """Fine-grained terminal classification for a flow run.

    Layered on top of ``FlowRunStatus``: ``status`` answers "did it
    finish?", ``outcome`` answers "and what kind of finish was it?".
    The two overlap intentionally — most agent-mode runs land
    ``status=succeeded + outcome=success`` — but the no-agent paths
    add several non-error outcomes (silent_2xx, nonempty_output) that
    the user surface treats specially: they are NOT failures even
    though they did not yield a regular ``output_summary``.
    """

    PENDING = "pending"
    SUCCESS = "success"
    SILENT_2XX = "silent_2xx"
    NONEMPTY_OUTPUT = "nonempty_output"
    ESCALATED_TO_AGENT = "escalated_to_agent"
    HTTP_ERROR = "http_error"
    SCRIPT_ERROR = "script_error"
    TIMEOUT = "timeout"
    SSRF_BLOCKED = "ssrf_blocked"
    VALIDATION_FAILED = "validation_failed"
    CANCELLED = "cancelled"
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
    # For NO_AGENT_SCRIPT: {"script_command": "...", "script_timeout_s": 60, ...}
    # For NO_AGENT_HTTP:   {"http_url": "...", "http_method": "GET", ...}
    trigger_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    execution_mode: Mapped[FlowExecutionMode] = mapped_column(
        Enum(FlowExecutionMode, native_enum=False, length=40),
        default=FlowExecutionMode.AGENT,
        server_default=FlowExecutionMode.AGENT.value,
        nullable=False,
        index=True,
    )

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
    outcome: Mapped[FlowRunOutcome | None] = mapped_column(
        Enum(FlowRunOutcome, native_enum=False, length=40),
        default=FlowRunOutcome.PENDING,
        server_default=FlowRunOutcome.PENDING.value,
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Probe metadata (no-agent modes only). Body / stdout themselves are not
    # persisted in full — only the first 4 KB excerpt — so this row stays
    # cheap regardless of what the upstream returns.
    probe_response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_output_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-node trace for the visual DAG. Each entry:
    # ``{node_id, status, started_at, finished_at, input, output, error}``.
    # Empty list for legacy (non-graph) flow runs.
    node_events_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    triggered_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
