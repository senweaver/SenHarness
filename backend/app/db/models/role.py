"""Custom workspace roles (beyond the built-in enum)."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class BuiltinRole(StrEnum):
    """Canonical roles every workspace ships with.

    Hierarchy (low → high privilege):

      GUEST < AUDITOR < MEMBER < OPERATOR < ADMIN < OWNER

    See ``app.services.permissions`` for the full capability matrix.
    """

    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    MEMBER = "member"
    AUDITOR = "auditor"
    GUEST = "guest"


class Role(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_roles_workspace_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permissions_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_system: Mapped[bool] = mapped_column(default=False, nullable=False)
