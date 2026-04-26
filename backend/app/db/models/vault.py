"""Vault — encrypted credential store with envelope-encryption metadata."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class VaultItemKind(StrEnum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    COOKIE_BAG = "cookie_bag"
    PASSWORD = "password"
    CERT = "cert"
    GENERIC = "generic"


class VaultItem(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    """Envelope-encrypted secret.

    Encryption layout (see `app.security.crypto.Sealed`):
      - `ciphertext`: Fernet(DEK).encrypt(plaintext_bytes)
      - `wrapped_dek`: KEK.encrypt(DEK)
      - `kek_version`: which KEK to unwrap with
    """

    __tablename__ = "vault_items"

    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[VaultItemKind] = mapped_column(
        String(32), default=VaultItemKind.GENERIC, nullable=False
    )

    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    acl_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    required_approval: Mapped[bool] = mapped_column(default=False, nullable=False)

    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class KekKey(UuidPkMixin, TimestampMixin, Base):
    """Ledger of KEK versions (for auditing and rotation plans)."""

    __tablename__ = "kek_keys"

    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retired_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
