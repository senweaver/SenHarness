"""Audit + moderation DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.agent_report import ReportReason, ReportStatus
from app.schemas._base import ORMModel


class AuditEventRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    actor_identity_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    summary: str | None
    metadata_json: dict
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AuditEventEnriched(AuditEventRead):
    """Audit row with display-friendly joined fields for the UI."""

    actor_name: str | None = None
    actor_email: str | None = None


class AgentReportIn(ORMModel):
    reason: ReportReason
    detail: str | None = Field(default=None, max_length=2000)


class AgentReportDecide(ORMModel):
    decision: ReportStatus  # reviewed / dismissed / removed
    note: str | None = Field(default=None, max_length=2000)


class AgentReportRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    reporter_identity_id: uuid.UUID | None
    reason: ReportReason
    detail: str | None
    status: ReportStatus
    review_decision: str | None
    reviewed_by_identity_id: uuid.UUID | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class AgentReportEnriched(AgentReportRead):
    agent_name: str | None = None
    agent_workspace_id: uuid.UUID | None = None
    reporter_name: str | None = None
    reviewer_name: str | None = None
