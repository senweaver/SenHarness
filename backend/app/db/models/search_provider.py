"""Workspace-scoped search provider catalogue.

Mirrors the LLM ``model_providers`` design but for web-search APIs (Tavily,
SerpAPI, Brave, Jina, Exa, ...). Keys live in vault and are unwrapped on the
fly by `app.agents.tools.web_search`.

Search APIs don't have a ``/v1/models`` discover endpoint, so this table is
plain-CRUD: one row per (workspace, kind), with ``vault_item_id`` pointing
at the encrypted secret.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SearchProviderKind(StrEnum):
    """Built-in search backends. Extensible — `kind` is a free String."""

    TAVILY = "tavily"
    SERPAPI = "serpapi"
    BRAVE = "brave"
    JINA = "jina"
    EXA = "exa"
    DUCKDUCKGO = "duckduckgo"  # no key needed; row exists for enable/disable


class SearchProvider(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "search_providers"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    vault_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vault_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
