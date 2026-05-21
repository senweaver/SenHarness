"""Identity / profile DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.db.models.identity import IdentityStatus, PlatformRole
from app.schemas._base import ORMModel, Timestamped


class IdentityRead(Timestamped):
    email: EmailStr
    name: str
    avatar_url: str | None = None
    status: IdentityStatus
    platform_role: PlatformRole
    oauth_provider: str | None = None
    profile_json: dict = Field(default_factory=dict)
    onboarded_at: datetime | None = None


class IdentityUpdate(ORMModel):
    name: str | None = None
    avatar_url: str | None = None
    profile_json: dict | None = None


class PasswordChangeIn(ORMModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class MeOut(IdentityRead):
    workspaces: list[MembershipBrief] = Field(default_factory=list)
    current_workspace_id: uuid.UUID | None = None
    # Role + department + capability list for the active workspace. Frontend
    # uses this to gate UI widgets without an extra round trip.
    current_role: str | None = None
    current_department_id: uuid.UUID | None = None
    permissions: list[str] = Field(default_factory=list)


class MembershipBrief(ORMModel):
    workspace_id: uuid.UUID
    workspace_name: str
    workspace_slug: str
    role: str
    department_id: uuid.UUID | None = None


MeOut.model_rebuild()
