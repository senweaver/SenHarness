"""DTOs for the M3.4 Agent Profile surface.

Two read shapes exist:

* :class:`AgentProfileRead` — workspace-member view; never carries
  ``cross_workspace_stats``.
* :class:`AgentProfileAdminRead` — platform-admin view; identical
  payload **plus** the cross-workspace rollup. Service-layer is the
  enforcement point — both routes go through the same ORM row.

The aggregation envelopes (:class:`StrengthsEnvelope`,
:class:`FailureModesEnvelope`) are intentionally open dicts at the
field level so future aggregation passes can add new buckets without
a migration; the documented schema lives under
``docs/extensions-and-governance.md`` (Profiles → Agent profile).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas._base import ORMModel, Timestamped


class AgentProfileRead(Timestamped):
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    strengths_json: dict = Field(default_factory=dict)
    failure_modes_json: dict = Field(default_factory=dict)
    last_aggregated_at: datetime | None = None
    aggregated_run_count: int = 0
    sample_size: int = 0


class AgentProfileAdminRead(AgentProfileRead):
    """Platform-admin view — same row plus the cross-workspace rollup."""

    cross_workspace_stats_json: dict = Field(default_factory=dict)


class AgentProfileRefreshResult(ORMModel):
    """Response of ``POST /agents/{agent_id}/profile/refresh``."""

    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    last_aggregated_at: datetime | None
    aggregated_run_count: int
    sample_size: int
    strengths_json: dict = Field(default_factory=dict)
    failure_modes_json: dict = Field(default_factory=dict)
    aux_skipped: bool = False
    aux_skip_reason: str | None = None
