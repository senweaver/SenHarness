"""Model provider + key + route.

Design goals:
  - A provider = external LLM endpoint (openai, anthropic, azure, openrouter,
    deepseek, moonshot, groq, ollama, vllm, sglang, local, ...).
  - Keys are encrypted in Vault (`vault_ref` points at a `vault_items.id`).
  - A route is a named rule set deciding which provider+model to use, optionally
    with fallbacks (used by `ProviderRouter` capability in P2+).
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class ProviderKind(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    AZURE_OPENAI = "azure_openai"
    DEEPSEEK = "deepseek"
    MOONSHOT = "moonshot"
    GROQ = "groq"
    OLLAMA = "ollama"
    VLLM = "vllm"
    SGLANG = "sglang"
    CUSTOM = "custom"


class ModelProvider(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "model_providers"

    kind: Mapped[ProviderKind] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ModelKey(UuidPkMixin, TimestampMixin, Base):
    """Encrypted API key bound to a provider."""

    __tablename__ = "model_keys"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Points at vault_items.id — actual secret lives there, envelope-encrypted.
    vault_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vault_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ModelRoute(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    """Named routing policy: which provider/model + fallbacks."""

    __tablename__ = "model_routes"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # rules_json: ordered list of {provider_id, model, when:{capabilities:[...], budget:{}}}
    rules_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Simple fallback chain of provider ids.
    fallback_order_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
