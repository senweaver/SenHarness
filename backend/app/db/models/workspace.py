"""Workspaces — tenants. Members / resources live inside a workspace."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin


class WorkspacePlan(StrEnum):
    FREE = "free"
    TEAM = "team"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class WorkspaceType(StrEnum):
    """Semantic label for the workspace — drives UI wording only.

    Does NOT change RBAC, data isolation, or billing; every workspace
    is a full tenant with the same access-control semantics. The label
    tells the UI how to talk about the space:

        COMPANY     → "Company settings" / "Company knowledge base"
        DEPARTMENT  → "Department settings" / used when a group company
                      splits per-BU workspaces
        TEAM        → "Team settings" — small project or squad
        PROJECT     → "Project settings" — time-bounded collaboration
        TENANT      → "Tenant settings" — SaaS-style customer isolation

    Operators can change this at any time; it's persisted as a string
    column so future labels (e.g. ``PRACTICE`` for law firms) can be
    added without a migration.
    """

    COMPANY = "company"
    DEPARTMENT = "department"
    TEAM = "team"
    PROJECT = "project"
    TENANT = "tenant"


DEFAULT_BRANDING: dict = {
    "agent_term": "agent",
    "welcome_h1": "你好，{name}。今天我们做点什么？",
    "primary_color": "#2E5BFF",
    "logo_url": None,
}


class Workspace(UuidPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    plan: Mapped[WorkspacePlan] = mapped_column(
        String(32), default=WorkspacePlan.FREE, nullable=False
    )

    # Semantic hint for the UI (see :class:`WorkspaceType`). Free-form
    # string in the DB so future labels don't need a migration.
    workspace_type: Mapped[str] = mapped_column(
        String(32), default=WorkspaceType.COMPANY, nullable=False, server_default="company"
    )

    branding_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    home_config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    quota_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
