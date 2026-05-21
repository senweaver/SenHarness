"""DTOs for persistent skill packs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.skills import SkillPackSource, SkillPackState
from app.schemas._base import ORMModel, Timestamped


class SkillPackCreate(ORMModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-_]{0,62}$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    version: str = Field(default="0.1.0", max_length=32)
    publisher: str | None = Field(default=None, max_length=128)
    signature: str | None = Field(default=None, max_length=512)
    source: SkillPackSource = SkillPackSource.WORKSPACE
    manifest_json: dict = Field(default_factory=dict)
    content_md: str = Field(min_length=1, max_length=400_000)
    enabled: bool = True
    metadata_json: dict = Field(default_factory=dict)


class SkillPackUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    version: str | None = Field(default=None, max_length=32)
    publisher: str | None = Field(default=None, max_length=128)
    signature: str | None = Field(default=None, max_length=512)
    source: SkillPackSource | None = None
    manifest_json: dict | None = None
    enabled: bool | None = None
    metadata_json: dict | None = None
    # When either of these is non-null the PATCH route routes through
    # the M1.2 ``skill_version`` service: a new SkillPackVersion is
    # snapshotted and activated, mirroring the body back onto the
    # SkillPack cache columns. Direct writes to ``SkillPack.content_md``
    # are no longer supported by the API.
    content_md: str | None = Field(default=None, min_length=1, max_length=400_000)
    files_json: dict[str, str] | None = None


class SkillPackRead(Timestamped):
    workspace_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    version: str
    publisher: str | None
    signature: str | None
    source: SkillPackSource
    manifest_json: dict
    enabled: bool
    metadata_json: dict
    created_by: uuid.UUID | None
    state: SkillPackState
    pinned: bool
    last_used_at: datetime | None = None
    effectiveness_avg: float | None = None
    content_hash: str | None = None
    superseded_by_pack_id: uuid.UUID | None = None
    state_changed_at: datetime | None = None
    state_changed_by: uuid.UUID | None = None


class SkillPackContent(ORMModel):
    pack: SkillPackRead
    content_md: str


class AgentSkillBindIn(ORMModel):
    skill_pack_ids: list[uuid.UUID] = Field(default_factory=list)


# ── M1.1 lifecycle DTOs ─────────────────────────────────────
class SkillPackTransitionRequest(ORMModel):
    """Body for ``POST /skills/packs/{id}/transitions``."""

    target_state: SkillPackState
    reason: str = Field(min_length=1, max_length=512)


class SkillPackTransitionEntry(ORMModel):
    """One row from the transition history."""

    from_state: SkillPackState | None = None
    to_state: SkillPackState | None = None
    reason: str | None = None
    actor_identity_id: uuid.UUID | None = None
    actor_kind: str | None = None
    occurred_at: datetime


class SkillPackStateResponse(ORMModel):
    """``GET /skills/packs/{id}/state`` payload."""

    pack_id: uuid.UUID
    state: SkillPackState
    pinned: bool
    state_changed_at: datetime | None = None
    state_changed_by: uuid.UUID | None = None
    last_transition: SkillPackTransitionEntry | None = None


class SkillPackTransitionList(ORMModel):
    """``GET /skills/packs/{id}/transitions`` payload."""

    pack_id: uuid.UUID
    items: list[SkillPackTransitionEntry]


class SkillPackActionReason(ORMModel):
    """Body for the verb routes (pin / unpin / archive / restore /
    deprecate). Reason is optional — the service supplies a stable
    fallback string when omitted so audit rows are never empty.
    """

    reason: str | None = Field(default=None, max_length=512)
