"""Schemas for the /metrics/usage endpoints."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class UsageSummary(BaseModel):
    """Totals across the selected window."""

    input_tokens: int
    output_tokens: int
    cost_usd: float
    turns: int
    sessions: int
    avg_latency_ms: float


class UsageDailyBucket(BaseModel):
    """One day of usage."""

    date: date
    input_tokens: int
    output_tokens: int
    cost_usd: float
    turns: int


class UsageByAgent(BaseModel):
    agent_id: str | None
    agent_name: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    turns: int


class UsageByModel(BaseModel):
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    turns: int


class UsageReport(BaseModel):
    """Aggregate payload returned by GET /metrics/usage (one call, all panels).

    A single round-trip populates the entire dashboard — easier to cache and
    avoids waterfalls on the client.
    """

    since: date
    until: date
    scope: str  # "me" | "workspace"
    summary: UsageSummary
    daily: list[UsageDailyBucket]
    top_agents: list[UsageByAgent]
    top_models: list[UsageByModel]
