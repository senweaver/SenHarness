"""Pydantic DTOs for pending-memory queue rows (M0.7).

Two surface shapes:

* :class:`PendingMemoryRead` — what the per-session list endpoint and
  the cancel mutation return. ``payload`` is exposed verbatim because
  the agent (and the human reviewer) need to see exactly what would
  land if the row promotes.
* :class:`PendingMemoryStats` — aggregate counts by status for the
  workspace-admin dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.pending_memory import (
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.schemas._base import ORMModel, Timestamped


class PendingMemoryRead(Timestamped):
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    identity_id: uuid.UUID | None
    target_table: PendingMemoryTargetTable
    payload: dict
    status: PendingMemoryStatus
    promoted_at: datetime | None
    promoted_target_id: uuid.UUID | None
    failure_reason: str | None
    failure_count: int


class PendingMemoryStats(ORMModel):
    workspace_id: uuid.UUID
    pending: int = Field(ge=0, default=0)
    promoted: int = Field(ge=0, default=0)
    skipped: int = Field(ge=0, default=0)
    failed: int = Field(ge=0, default=0)
    oldest_pending_at: datetime | None = None


class PromoteSweepResult(ORMModel):
    """Returned by the platform-admin debug ``promote-now`` endpoint."""

    workspaces_visited: int
    promoted: int
    skipped: int
    failed: int
