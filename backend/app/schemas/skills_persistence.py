"""DTOs for persistent skill packs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.skills import SkillPackSource
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


class SkillPackContent(ORMModel):
    pack: SkillPackRead
    content_md: str


class AgentSkillBindIn(ORMModel):
    skill_pack_ids: list[uuid.UUID] = Field(default_factory=list)
