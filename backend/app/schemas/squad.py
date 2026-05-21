"""Squad DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.squad import SquadStrategy
from app.schemas._base import ORMModel, Timestamped


class SquadMemberIn(ORMModel):
    agent_id: uuid.UUID
    role_in_squad: str = Field(default="member", max_length=64)
    weight: int = Field(default=0, ge=0)


class SquadCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    strategy: SquadStrategy = SquadStrategy.ROUTER
    config_json: dict = Field(default_factory=dict)
    members: list[SquadMemberIn] = Field(default_factory=list)


class SquadUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    strategy: SquadStrategy | None = None
    config_json: dict | None = None


class SquadMemberRead(Timestamped):
    squad_id: uuid.UUID
    agent_id: uuid.UUID
    role_in_squad: str
    weight: int


class SquadRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None
    strategy: SquadStrategy
    config_json: dict
    created_by: uuid.UUID | None = None


class SquadReadWithMembers(SquadRead):
    members: list[SquadMemberRead] = Field(default_factory=list)


class StarSquadOut(ORMModel):
    squad_id: uuid.UUID
    starred: bool
    pinned: bool
