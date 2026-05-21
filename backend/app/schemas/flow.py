"""Flow + FlowRun DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.db.models.flow import (
    FlowExecutionMode,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.schemas._base import ORMModel, Timestamped

# Hard caps mirrored in the service layer; keep both in sync.
SCRIPT_TIMEOUT_DEFAULT_S = 60
SCRIPT_TIMEOUT_MAX_S = 600
HTTP_TIMEOUT_DEFAULT_S = 30
HTTP_TIMEOUT_MAX_S = 120
HTTP_BODY_MAX_BYTES = 64 * 1024


class ScriptModeConfig(BaseModel):
    """Validates the script-mode keys inside ``Flow.trigger_config``.

    The instance never round-trips to the DB directly; the service layer
    persists the raw ``trigger_config`` dict. We only use the parsed
    object as a typed view + a 422 surface during create / update.
    """

    script_command: str = Field(min_length=1, max_length=8192)
    script_cwd: str | None = Field(default=None, max_length=512)
    script_timeout_s: int = Field(
        default=SCRIPT_TIMEOUT_DEFAULT_S, ge=1, le=SCRIPT_TIMEOUT_MAX_S
    )
    script_env: dict[str, str] | None = None
    escalate_on_nonempty_output: bool = True

    @model_validator(mode="after")
    def _validate_env(self) -> ScriptModeConfig:
        if self.script_env is None:
            return self
        for k, v in self.script_env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("script_env must map str -> str")
            # Keys are typed-but-shell-evaluatable env names; we deny shell
            # metacharacters to keep the variable from accidentally re-entering
            # the shell expansion path.
            if any(c in k for c in "$`\\\n;&|<>"):
                raise ValueError(f"script_env key contains forbidden char: {k!r}")
        return self


HttpMethod = Literal["GET", "HEAD", "POST"]


class HttpModeConfig(BaseModel):
    """Validates the HTTP-mode keys inside ``Flow.trigger_config``.

    SSRF guarding happens in the service layer (it needs DNS resolution
    + workspace-scoped allowlists later); validators here cover the
    static surface only.
    """

    http_url: str = Field(min_length=1, max_length=2048)
    http_method: HttpMethod = "GET"
    http_headers: dict[str, str] = Field(default_factory=dict)
    http_body: str | None = Field(default=None, max_length=HTTP_BODY_MAX_BYTES)
    http_timeout_s: int = Field(
        default=HTTP_TIMEOUT_DEFAULT_S, ge=1, le=HTTP_TIMEOUT_MAX_S
    )
    http_expected_status: list[int] | None = None
    escalate_on_http_failure: bool = True

    @model_validator(mode="after")
    def _validate(self) -> HttpModeConfig:
        for k, v in self.http_headers.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("http_headers must map str -> str")
            if "\r" in k or "\n" in k or "\r" in v or "\n" in v:
                raise ValueError("http_headers contain CR/LF (header injection)")
        if self.http_method != "POST" and self.http_body:
            raise ValueError("http_body is only allowed for POST")
        if self.http_expected_status is not None:
            for code in self.http_expected_status:
                if not (100 <= code <= 599):
                    raise ValueError(
                        f"http_expected_status entry {code} out of range"
                    )
        return self


def _validate_mode_payload(
    *, execution_mode: FlowExecutionMode | None, trigger_config: dict | None
) -> None:
    """422 on mode/config mismatch — shared by create + patch."""
    if execution_mode is None:
        return
    cfg = trigger_config or {}
    if execution_mode == FlowExecutionMode.NO_AGENT_SCRIPT:
        ScriptModeConfig.model_validate(cfg)
    elif execution_mode == FlowExecutionMode.NO_AGENT_HTTP:
        HttpModeConfig.model_validate(cfg)


class FlowCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    trigger_kind: FlowTriggerKind = FlowTriggerKind.MANUAL
    trigger_config: dict = Field(default_factory=dict)
    execution_mode: FlowExecutionMode = FlowExecutionMode.AGENT
    agent_id: uuid.UUID | None = None
    squad_id: uuid.UUID | None = None
    # Classic mode (D4): non-empty prompt template. Visual mode (D14): this
    # can be empty as long as graph_json is populated. No-agent modes don't
    # use the prompt at all but we keep the column non-null for legacy SQL.
    prompt_template: str = ""
    graph_json: dict = Field(default_factory=dict)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_mode(self) -> FlowCreate:
        _validate_mode_payload(
            execution_mode=self.execution_mode,
            trigger_config=self.trigger_config,
        )
        return self


class FlowUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    trigger_kind: FlowTriggerKind | None = None
    trigger_config: dict | None = None
    execution_mode: FlowExecutionMode | None = None
    agent_id: uuid.UUID | None = None
    squad_id: uuid.UUID | None = None
    prompt_template: str | None = None
    graph_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> FlowUpdate:
        # Only validate when the patch actually flips into a no-agent
        # mode and supplies a config; partial updates that touch only one
        # of the two fields are validated again at the service boundary
        # against the merged final state.
        if self.execution_mode is not None and self.trigger_config is not None:
            _validate_mode_payload(
                execution_mode=self.execution_mode,
                trigger_config=self.trigger_config,
            )
        return self


class FlowRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    trigger_kind: FlowTriggerKind
    trigger_config: dict
    execution_mode: FlowExecutionMode
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
    outcome: FlowRunOutcome | None = None
    started_at: datetime | None
    finished_at: datetime | None
    output_summary: str | None
    error: str | None
    probe_response_status: int | None = None
    probe_duration_ms: int | None = None
    probe_output_excerpt: str | None = None
    # Per-node trace for visual DAG runs; empty for classic-mode runs.
    node_events_json: list = Field(default_factory=list)
    triggered_by_identity_id: uuid.UUID | None
    created_at: datetime


class FlowManualTrigger(ORMModel):
    payload: dict = Field(default_factory=dict)


class FlowTestResult(ORMModel):
    """Response body for ``POST /flows/{id}/test-{script,http}``.

    The endpoint is a dry-run — it does NOT create a FlowRun row, does
    NOT enqueue any background work, does NOT touch the agent loop. It
    returns enough information for the admin UI to render a "looks
    good" / "would have failed" panel.
    """

    outcome: FlowRunOutcome
    duration_ms: int
    response_status: int | None = None
    exit_code: int | None = None
    output_excerpt: str | None = None
    error: str | None = None
