"""DTOs for the M1.3 skill usage telemetry surface."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.skill_usage import SkillUsageEventKind
from app.schemas._base import ORMModel, Timestamped


class SkillUsageRead(Timestamped):
    workspace_id: uuid.UUID
    pack_id: uuid.UUID
    version_id: uuid.UUID | None = None
    run_id: uuid.UUID
    session_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    identity_id: uuid.UUID | None = None
    event_kind: SkillUsageEventKind
    contribution_score: float | None = None


class SkillUsageList(ORMModel):
    pack_id: uuid.UUID
    items: list[SkillUsageRead] = Field(default_factory=list)


class SkillUsageStats(ORMModel):
    """Aggregated stats for one SkillPack.

    ``window_days`` = the lookback used by the aggregation.
    ``contribution_avg`` is ``None`` when no row in the window carried
    a non-null ``contribution_score`` — the UI renders an em-dash for
    that case rather than zero.
    """

    pack_id: uuid.UUID
    window_days: int
    use_count: int
    last_used_at: datetime | None = None
    contribution_avg: float | None = None
    use_count_by_kind: dict[str, int] = Field(default_factory=dict)
    trend_7d: dict[str, int] = Field(default_factory=dict)


class SkillUsageRollupResult(ORMModel):
    """Response of the manual rollup endpoint."""

    pack_id: uuid.UUID
    last_used_at: datetime | None = None
    effectiveness_avg: float | None = None
    use_count: int
    rolled_up_at: datetime
