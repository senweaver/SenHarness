"""Pydantic DTOs for the cross-session insights API (M4.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class InsightsGenerateRequest(BaseModel):
    """Body for ``POST /insights/generate``.

    ``return_session_id`` is the session the rendered markdown lands
    in. Validated as a workspace-scoped session_id by the route
    handler before the call is queued. ``days`` is bounded by the
    workspace's :class:`InsightsSettings.max_days` — out-of-range
    values raise ``insights.days_out_of_range``.
    """

    return_session_id: uuid.UUID
    days: Annotated[int | None, Field(ge=1, le=180)] = None


class InsightsGenerateResponse(BaseModel):
    queued: bool
    days: int
    expected_completion_seconds: int
    job_id: str | None = None


class InsightsRunSummary(BaseModel):
    """One row in ``GET /insights/recent``.

    Sourced from ``audit_events.action='insights.cross_session_summarized'``;
    metadata fields back ``days`` / ``artifact_count`` / ``item_count``
    / ``aux_model`` / ``degraded``. The ``session_id`` lets the UI
    deep-link back to the session that received the markdown reply.
    """

    audit_event_id: uuid.UUID
    session_id: uuid.UUID | None
    created_at: datetime
    days: int
    artifact_count: int
    item_count: int
    aux_model: str | None = None
    degraded: bool = False


class InsightsRecentResponse(BaseModel):
    items: list[InsightsRunSummary]
