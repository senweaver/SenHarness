"""Workspace-level curator configuration DTOs (M1.9).

Surfaces the four knobs the workspace admin can tune in
``/settings/workspace/skills``:

* ``enabled`` — master switch for the nightly Curator sweep.
* ``stale_after_days`` — ACTIVE pack idle threshold for STALE proposals.
* ``archive_after_days`` — STALE pack age threshold for archive proposals.
* ``min_idle_hours`` — guard window against racing a fresh use-event the
  rollup cron is about to write.
* ``active_skills_soft_cap`` — soft cap consulted by future Curator
  passes; today the M1.8 runtime cap handles hard truncation, this knob
  is plumbed for the M1.9 admin UI + the M1.4 Curator's slow ramp.

The merged config (workspace override > platform default) plus a
``source`` map telling the UI which knob came from which tier. The
schemas mirror :class:`app.services.system_settings.CuratorDefaults`
(M1.4) — both layers must accept the same int ranges so a workspace
admin can never persist a value the platform default schema would
reject.

Cross-field invariant: ``stale_after_days <= archive_after_days``.
A STALE pack must wait at least one day of idleness before the
Curator graduates it to an archive proposal; reversing the relation
would file archive proposals on packs that just transitioned to
STALE inside the same sweep, which is hostile UX.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CuratorFieldSource = Literal["workspace", "platform_default"]


class CuratorConfigIn(BaseModel):
    """Inbound PATCH body. All knobs optional — None means "leave the
    workspace override alone if set, otherwise keep falling back to the
    platform default". The PATCH endpoint merges this onto the existing
    ``home_config_json["curator"]`` block.
    """

    enabled: bool | None = None
    stale_after_days: int | None = Field(default=None, ge=1, le=365)
    archive_after_days: int | None = Field(default=None, ge=1, le=365)
    min_idle_hours: int | None = Field(default=None, ge=0, le=720)
    active_skills_soft_cap: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _check_stale_le_archive(self) -> CuratorConfigIn:
        if (
            self.stale_after_days is not None
            and self.archive_after_days is not None
            and self.stale_after_days > self.archive_after_days
        ):
            raise ValueError(
                "stale_after_days must be less than or equal to "
                "archive_after_days"
            )
        return self


class CuratorConfigOut(BaseModel):
    """Merged config returned by GET / PATCH. Every knob is fully
    populated (after applying workspace > platform fallback) so the UI
    never has to know about defaults; ``source`` reports which tier
    each knob came from so the admin can tell at a glance what they've
    customised.
    """

    enabled: bool
    stale_after_days: int = Field(ge=1, le=365)
    archive_after_days: int = Field(ge=1, le=365)
    min_idle_hours: int = Field(ge=0, le=720)
    active_skills_soft_cap: int = Field(ge=1, le=1000)
    source: dict[str, CuratorFieldSource]


class CuratorRunResult(BaseModel):
    """One curator_tick outcome. Mirrors the dict the M1.4 service
    returns from :func:`trigger_curator_now`; the four counts are
    guaranteed non-negative and ``finished_at >= started_at``.
    """

    workspace_id: uuid.UUID
    stale_proposed: int = Field(ge=0)
    archive_proposed: int = Field(ge=0)
    pinned_skipped: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime


class CuratorLastRunOut(BaseModel):
    """Snapshot of the most recent curator_tick + the next scheduled
    run. ``last_run_at``/``last_result`` are None on workspaces that
    have never been swept. ``upcoming_run_at`` is ``None`` when the
    cron is disabled or the schedule cannot be inferred (curator
    service not yet wired in).
    """

    last_run_at: datetime | None
    last_result: CuratorRunResult | None
    upcoming_run_at: datetime | None
