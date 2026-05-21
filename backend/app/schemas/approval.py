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
    # Nullable since M1.4 — non-tool approvals (Curator archive, M2
    # evolver verbs, M2.8 cron flow) carry no chat session.
    session_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None
    run_id: uuid.UUID | None
    tool_name: str
    tool_args: dict[str, Any]
    summary: str | None
    status: str
    # M1.4 wide-approval routing key (None on legacy tool-call rows).
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    requested_by_identity_id: uuid.UUID | None
    decided_by_identity_id: uuid.UUID | None
    decided_reason: str | None
    decided_at: datetime | None
    expires_at: datetime | None
    # M2.5 — pre-expiry reminder fired by the TTL processor (default
    # False on every existing row; admins can ignore in the UI).
    reminder_sent: bool = False
    created_at: datetime
    # Enrichment populated by the API layer, not ORM — optional so callers
    # that don't fetch it don't have to lie about it.
    requester_department_name: str | None = None
    decided_by_department_name: str | None = None


class DispatchResultRead(BaseModel):
    """M2.5 — outcome of the dispatch handler returned alongside ApprovalRead.

    Populated only on the approve path; rejects + bulk + legacy
    tool-call rows leave it ``None``. ``applied_object_id`` lets the
    UI deep-link to the new SkillPackVersion / Flow / archived pack.
    """

    approval_id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID | None = None
    applied_object_id: uuid.UUID | None = None
    audit_action: str


class ApprovalDecisionResponse(BaseModel):
    """M2.5 — REST decision endpoint payload (approve only).

    Wraps the existing ``ApprovalRead`` body and tags on the optional
    dispatch result so the frontend can render a "View archived pack /
    new version / new flow" button immediately after approving.
    """

    approval: ApprovalRead
    dispatch_result: DispatchResultRead | None = None


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
