"""Flow + FlowRun DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.flow import FlowRunStatus, FlowTriggerKind
from app.schemas._base import ORMModel, Timestamped


class FlowCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    trigger_kind: FlowTriggerKind = FlowTriggerKind.MANUAL
    trigger_config: dict = Field(default_factory=dict)
    agent_id: uuid.UUID | None = None
    squad_id: uuid.UUID | None = None
    # Classic mode (D4): non-empty prompt template. Visual mode (D14): this
    # can be empty as long as graph_json is populated.
    prompt_template: str = ""
    graph_json: dict = Field(default_factory=dict)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class FlowUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    trigger_kind: FlowTriggerKind | None = None
    trigger_config: dict | None = None
    agent_id: uuid.UUID | None = None
    squad_id: uuid.UUID | None = None
    prompt_template: str | None = None
    graph_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None


class FlowRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    trigger_kind: FlowTriggerKind
    trigger_config: dict
    agent_id: uuid.UUID | None
    squad_id: uuid.UUID | None
    prompt_template: str
    graph_json: dict
    enabled: bool
    last_run_at: datetime | None
    metadata_json: dict
    created_by: uuid.UUID | None = None


class FlowRunRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    flow_id: uuid.UUID
    session_id: uuid.UUID | None
    trigger_kind: FlowTriggerKind
    trigger_payload_json: dict
    status: FlowRunStatus
    started_at: datetime | None
    finished_at: datetime | None
    output_summary: str | None
    error: str | None
    # Per-node trace for visual DAG runs; empty for classic-mode runs.
    node_events_json: list = Field(default_factory=list)
    triggered_by_identity_id: uuid.UUID | None
    created_at: datetime


class FlowManualTrigger(ORMModel):
    payload: dict = Field(default_factory=dict)
