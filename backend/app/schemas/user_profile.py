"""DTOs for the M3.7 Honcho-style 12-dimension user profile surface."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.user_profile import UserProfileDimension
from app.schemas._base import ORMModel, Timestamped


class UserProfileFactRead(Timestamped):
    """One persisted fact row (rendered for the ``/me/profile`` UI)."""

    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    dimension: UserProfileDimension
    fact: str
    confidence: float
    source_run_ids: list[str] = Field(default_factory=list)
    superseded_by_id: uuid.UUID | None = None
    user_confirmed: bool = False
    user_rejected: bool = False


class UserProfileDimensionView(ORMModel):
    """Per-dimension snapshot — current active fact + recent history.

    ``active`` is the row the renderer injects (or ``None`` if every
    candidate is rejected / pending below the auto-inject threshold).
    ``history`` carries up to 10 prior candidates so the UI can show
    the dialectic chain that led to the current state.
    """

    dimension: UserProfileDimension
    active: UserProfileFactRead | None = None
    history: list[UserProfileFactRead] = Field(default_factory=list)
    pending_count: int = 0
    rejected_count: int = 0


class UserProfileBundle(ORMModel):
    """Full ``GET /me/profile`` payload — one entry per dimension."""

    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    dimensions: list[UserProfileDimensionView]
    rendered_chars: int = Field(
        0,
        description=(
            "Estimated character count of the next system-prompt "
            "injection (after per-line + total trimming). Lets the "
            "UI show 'X / 4000 chars used'."
        ),
    )
    last_extracted_at: datetime | None = None


class UserProfileExtractNowResult(ORMModel):
    """Response of ``POST /me/profile/extract-now``.

    Mirrors the agent_profile refresh shape so the UI can show a
    single status banner regardless of whether extraction surfaced
    new facts or just touched existing rows.
    """

    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    facts_created: int = 0
    facts_superseded: int = 0
    facts_unchanged: int = 0
    artifacts_examined: int = 0
    aux_skipped: bool = False
    aux_skip_reason: str | None = None
    duration_ms: int = 0


__all__ = [
    "UserProfileBundle",
    "UserProfileDimensionView",
    "UserProfileExtractNowResult",
    "UserProfileFactRead",
]
