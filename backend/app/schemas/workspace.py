"""Workspace / member / role / department / invitation DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.db.models.membership import MembershipStatus
from app.db.models.workspace import WorkspacePlan, WorkspaceType
from app.schemas._base import ORMModel, Timestamped


# ─── Workspace ────────────────────────────────────────────
class WorkspaceCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str | None = None
    workspace_type: WorkspaceType = WorkspaceType.COMPANY


class WorkspaceUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    workspace_type: WorkspaceType | None = None
    branding_json: dict | None = None
    home_config_json: dict | None = None


class WorkspaceRead(Timestamped):
    name: str
    slug: str
    description: str | None = None
    plan: WorkspacePlan
    # Sent as a plain string so the frontend can display future labels
    # added by operators without the API refusing to validate.
    workspace_type: str = WorkspaceType.COMPANY
    branding_json: dict
    home_config_json: dict


# ─── Member ───────────────────────────────────────────────
class MemberRead(Timestamped):
    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    role: str
    department_id: uuid.UUID | None = None
    status: MembershipStatus
    # Joined identity fields (populated by /members). Nullable so the schema
    # keeps working when the endpoint doesn't supply them.
    identity_name: str | None = None
    identity_email: str | None = None
    identity_avatar_url: str | None = None


class MemberUpdate(ORMModel):
    role: str | None = None
    department_id: uuid.UUID | None = None
    status: MembershipStatus | None = None


# ─── Role ─────────────────────────────────────────────────
class RoleCreate(ORMModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    permissions_json: dict = Field(default_factory=dict)


class RoleRead(Timestamped):
    workspace_id: uuid.UUID
    name: str
    description: str | None = None
    permissions_json: dict
    is_system: bool


# ─── Department ───────────────────────────────────────────
class DepartmentCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: uuid.UUID | None = None


class DepartmentRead(Timestamped):
    workspace_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    name: str
    path: str


# ─── Invitation ───────────────────────────────────────────
class InvitationCreate(ORMModel):
    email: EmailStr | None = None
    role: str = "member"
    department_id: uuid.UUID | None = None
    expires_in_hours: int = 72


class InvitationRead(Timestamped):
    workspace_id: uuid.UUID
    code: str
    email: str | None
    role: str
    department_id: uuid.UUID | None
    expires_at: datetime | None
    used_at: datetime | None


class InvitationAccept(ORMModel):
    code: str
