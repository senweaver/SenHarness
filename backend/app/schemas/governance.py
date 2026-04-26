"""Governance DTOs: policies, budgets, usage events and tool logs."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import Field

from app.db.models.governance import BudgetPeriod, GovernanceScope
from app.schemas._base import ORMModel, Timestamped


class PolicyCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scope: GovernanceScope = GovernanceScope.WORKSPACE
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10_000)
    rules_json: dict = Field(default_factory=dict)
    metadata_json: dict = Field(default_factory=dict)


class PolicyUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scope: GovernanceScope | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    rules_json: dict | None = None
    metadata_json: dict | None = None


class PolicyRead(Timestamped):
    scope: GovernanceScope
    workspace_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    name: str
    description: str | None
    enabled: bool
    priority: int
    rules_json: dict
    metadata_json: dict
    created_by: uuid.UUID | None


class BudgetCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    scope: GovernanceScope = GovernanceScope.WORKSPACE
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    currency: str = Field(default="USD", min_length=3, max_length=8)
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    limit_amount: Decimal = Field(gt=0)
    alert_threshold_pct: int | None = Field(default=80, ge=1, le=100)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class BudgetUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    scope: GovernanceScope | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    period: BudgetPeriod | None = None
    limit_amount: Decimal | None = Field(default=None, gt=0)
    alert_threshold_pct: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None
    metadata_json: dict | None = None


class BudgetRead(Timestamped):
    scope: GovernanceScope
    workspace_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    name: str
    currency: str
    period: BudgetPeriod
    limit_amount: Decimal
    alert_threshold_pct: int | None
    enabled: bool
    metadata_json: dict
    created_by: uuid.UUID | None


class UsageEventCreate(ORMModel):
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None
    budget_id: uuid.UUID | None = None
    event_type: str = Field(min_length=1, max_length=64)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0)
    tool_name: str | None = Field(default=None, max_length=128)
    metadata_json: dict = Field(default_factory=dict)


class UsageEventRead(Timestamped):
    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    session_id: uuid.UUID | None
    policy_id: uuid.UUID | None
    budget_id: uuid.UUID | None
    event_type: str
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    tool_name: str | None
    metadata_json: dict


class ToolCallLogCreate(ORMModel):
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None
    tool_name: str = Field(min_length=1, max_length=128)
    status: str = Field(default="success", min_length=1, max_length=32)
    duration_ms: int | None = Field(default=None, ge=0)
    input_json: dict = Field(default_factory=dict)
    output_json: dict = Field(default_factory=dict)
    error_text: str | None = None
    cost_usd: Decimal | None = Field(default=None, ge=0)
    metadata_json: dict = Field(default_factory=dict)


class ToolCallLogRead(Timestamped):
    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    session_id: uuid.UUID | None
    policy_id: uuid.UUID | None
    tool_name: str
    status: str
    duration_ms: int | None
    input_json: dict
    output_json: dict
    error_text: str | None
    cost_usd: Decimal | None
    metadata_json: dict
