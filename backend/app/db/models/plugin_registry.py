"""Plugin registry — sha256 + signature + admin approval gate (M3.9).

Every folder under ``STORAGE_LOCAL_PATH/plugins/`` that the loader can
discover ends up as one row here. Discovery merely populates the row;
loading is gated separately by:

1. ``platform_settings.plugins.allow_user_plugins`` — master switch
   (default ``False``; default-deny per design principle 7).
2. ``signature`` matches the platform-wide trust root (ed25519
   verification of the folder ``sha256`` digest).
3. ``approved_by_platform_admin`` is ``True`` (set by an explicit
   admin click in the M3.9 admin console).

The loader never touches ``approved_by_platform_admin`` itself; it
only reads it. The admin console writes it via
:mod:`app.api.v1.admin_plugin`. That separation keeps the trust gate
out of the discovery hot path.

Indexes:

* ``ix_plugin_registry_name`` — admin search by plugin name.
* ``ix_plugin_registry_sha256`` — fast lookup during ``discover``.
* ``ix_plugin_registry_status`` — admin filter by status.
* unique ``(name, version, sha256)`` — multi-version coexistence
  while keeping a tampered drop-in detectable (re-hashing yields a
  different sha → new row, old row preserved for audit).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin


class PluginRegistryStatus(StrEnum):
    """Lifecycle of one plugin row.

    A plugin always starts at :attr:`DISCOVERED` (the loader saw the
    folder for the first time, no admin has decided yet). The gate
    pipeline transitions it forward — :attr:`SIGNED_VERIFIED` after
    the ed25519 check, :attr:`APPROVED` after the admin clicks
    "Approve", :attr:`LOADED` once the loader actually called the
    plugin's ``register(ctx)``. :attr:`REJECTED` is terminal: a
    rejected plugin never reloads, and a reupload with a different
    sha256 lands as a fresh row rather than reusing the rejected one.
    """

    DISCOVERED = "discovered"
    SIGNED_VERIFIED = "signed_verified"
    APPROVED = "approved"
    REJECTED = "rejected"
    LOADED = "loaded"


class PluginRegistry(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "plugin_registry"
    __table_args__ = (
        UniqueConstraint(
            "name", "version", "sha256", name="uq_plugin_registry_name_version_sha"
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)

    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    trust_root: Mapped[str | None] = mapped_column(String(120), nullable=True)

    approved_by_platform_admin: Mapped[bool] = mapped_column(
        nullable=False, default=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[PluginRegistryStatus] = mapped_column(
        Enum(
            PluginRegistryStatus,
            name="plugin_registry_status",
            native_enum=False,
            length=32,
            validate_strings=True,
        ),
        nullable=False,
        default=PluginRegistryStatus.DISCOVERED,
        server_default=PluginRegistryStatus.DISCOVERED.value,
        index=True,
    )

    capability_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    last_load_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_load_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    folder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = ["PluginRegistry", "PluginRegistryStatus"]
