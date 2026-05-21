"""DTOs for the M2.4 skill verifier endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.db.models.skill_pack_version import SkillPackVersionState
from app.schemas._base import ORMModel


class SkillVerifierRunResponse(ORMModel):
    """Wire shape for ``POST .../verify-now``.

    Mirrors :class:`app.services.skill_verifier.VerificationResult`
    minus the per-pair detail (callers use the validation read
    endpoint when they need the full pair list).
    """

    version_id: uuid.UUID
    status: Literal["accepted", "rejected", "skipped_insufficient", "errored"]
    old_score_avg: float | None = None
    new_score_avg: float | None = None
    score_delta: float | None = None
    replayed_artifacts: int = Field(ge=0)
    threshold: float = Field(ge=0.0, le=1.0)
    duration_ms: int = Field(ge=0)
    error: str | None = None


class SkillVerifierValidationResponse(ORMModel):
    """Wire shape for ``GET .../validation``.

    ``validation_results`` is the raw persisted JSONB blob so the UI
    can render whatever fields it knows about without a follow-up
    schema migration when the verifier stores new keys.
    """

    version_id: uuid.UUID
    pack_id: uuid.UUID
    version_no: int
    state: SkillPackVersionState
    judge_score: float | None = None
    validation_results: dict
