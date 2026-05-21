"""DTOs for SkillPackVersion endpoints (M1.2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.skill_pack_version import SkillPackVersionState
from app.schemas._base import ORMModel, Timestamped


class SkillPackVersionRead(Timestamped):
    workspace_id: uuid.UUID
    pack_id: uuid.UUID
    version_no: int
    content_hash: str
    state: SkillPackVersionState
    created_by: str
    creator_identity_id: uuid.UUID | None = None
    source_run_ids: list[str] = Field(default_factory=list)
    judge_score: float | None = None
    superseded_by_version_id: uuid.UUID | None = None
    activated_at: datetime | None = None
    retired_at: datetime | None = None


class SkillPackVersionWithContent(SkillPackVersionRead):
    """Heavy variant — used for the version-detail endpoint and the
    rollback preview path. Inflated payload kept off the list endpoint
    to keep the drawer load cheap.
    """

    content_md: str
    files_json: dict
    validation_results: dict


class SkillPackVersionList(ORMModel):
    pack_id: uuid.UUID
    items: list[SkillPackVersionRead]


class SkillPackVersionTransitionRequest(ORMModel):
    target_state: SkillPackVersionState
    reason: str = Field(min_length=1, max_length=512)


class SkillPackVersionActivateRequest(ORMModel):
    """Optional body for the activate endpoint — admins can attach a
    reason that ends up in audit metadata.
    """

    reason: str | None = Field(default=None, max_length=512)


class SkillRollbackRequest(ORMModel):
    """Body for the M1.6 rollback verb endpoint.

    ``reason`` is required because rollback rewrites which version is
    live and the operator history needs to distinguish a deliberate
    "I want last week's version back" from a routine activation. The
    400-char cap is tighter than ``SkillPackVersionTransitionRequest``
    so the audit summary stays scannable.
    """

    reason: str = Field(min_length=1, max_length=400)
