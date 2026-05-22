"""Model provider + key + route.

Design goals:
  - A provider = external LLM endpoint (openai, anthropic, azure, openrouter,
    deepseek, moonshot, groq, ollama, vllm, sglang, custom, ...).
  - Keys are encrypted in Vault (`vault_ref` points at a `vault_items.id`).
  - A route is a named rule set deciding which provider+model to use, optionally
    with fallbacks (used by `ProviderRouter` capability in P2+).

Note on ``kind``:
  Stored as a free-form ``String(64)`` so SenHarness inherits any provider
  pydantic-ai supports (currently 30) without schema migration. The
  ``ProviderKind`` enum below is kept as an IDE-friendly constant set, not as a
  database constraint — service-layer validation goes through
  :func:`app.agents.kernels.provider_catalog.is_known_kind`.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class ProviderKind(StrEnum):
    """Common provider kinds (constant set, not a DB-level enum).

    Validation lives in the service layer via the catalog whitelist, so any
    provider pydantic-ai exposes can be added without a schema migration.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    XAI = "xai"
    OPENROUTER = "openrouter"
    AZURE_OPENAI = "azure_openai"
    HUGGINGFACE = "huggingface"
    DEEPSEEK = "deepseek"
    DASHSCOPE = "dashscope"
    BAILIAN_TOKEN = "bailian_token"
    BAILIAN_CODING = "bailian_coding"
    MOONSHOT = "moonshot"
    KIMI_CODE = "kimi_code"
    ZHIPU = "zhipu"
    SILICONFLOW = "siliconflow"
    MINIMAX = "minimax"
    OLLAMA = "ollama"
    VLLM = "vllm"
    CUSTOM = "custom"


class CredentialType(StrEnum):
    """How the operator supplies credentials to a provider."""

    API_KEY = "api_key"
    OAuth_TOKEN = "oauth_token"
    CUSTOM_HEADERS = "custom_headers"


class ModelProvider(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "model_providers"

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    credential_type: Mapped[str] = mapped_column(
        String(32), default=CredentialType.API_KEY.value, nullable=False
    )
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class ProviderModel(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "provider_models"
    __table_args__ = (sa.UniqueConstraint("provider_id", "model", name="uq_provider_models_pk"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommended: Mapped[bool] = mapped_column(default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ModelKey(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "model_keys"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
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
    __tablename__ = "model_routes"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rules_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    fallback_order_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
