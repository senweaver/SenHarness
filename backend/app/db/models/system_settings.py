"""Platform-level (workspace-agnostic) key/value settings.

Used by M0.9+ to surface platform admin tunables (registration mode,
email-verification gate, reserved-slug allowlist, etc.) without
forcing a column-level migration for each new toggle. M0.13 wraps a
schema-driven admin UI on top; until then values are seeded with
service defaults and changed through CLI / direct SQL.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class SystemSetting(TimestampMixin, Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=True
    )
