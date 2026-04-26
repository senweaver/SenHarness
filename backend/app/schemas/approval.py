"""Approval DTOs for REST + WS."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas._base import ORMModel


class ApprovalRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    agent_id: uuid.UUID | None
    run_id: uuid.UUID | None
    tool_name: str
    tool_args: dict[str, Any]
    summary: str | None
    status: str
    requested_by_identity_id: uuid.UUID | None
    decided_by_identity_id: uuid.UUID | None
    decided_reason: str | None
    decided_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    # Enrichment populated by the API layer, not ORM — optional so callers
    # that don't fetch it don't have to lie about it.
    requester_department_name: str | None = None
    decided_by_department_name: str | None = None


class ApprovalDecision(BaseModel):
    action: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=500)


class BulkApprovalDecision(BaseModel):
    """Batch approve / deny payload.

    Each id is validated and decided independently. The endpoint returns
    detailed per-row outcomes instead of aborting on the first failure so the
    UI can mark successful rows green and surface the specific errors for the
    rest (missing row, already decided, no permission, …).
    """

    approval_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=500)


class BulkDecisionItem(BaseModel):
    approval_id: uuid.UUID
    ok: bool
    error_code: str | None = None
    error_message: str | None = None


class BulkDecisionResult(BaseModel):
    succeeded: list[uuid.UUID]
    failed: list[BulkDecisionItem]
