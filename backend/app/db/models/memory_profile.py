"""Four-layer memory profiles (V2 · Harness L1/L4).

Three profile kinds live in one table so CRUD, caps, and injection all share
code:

- ``workspace_memory`` — the **public knowledge core** of a workspace
  (``WORKSPACE MEMORY.md`` in Harness lingo). One row per workspace.
  Injected into every Agent system prompt as "what this company knows /
  believes / mandates". Admin-editable.
- ``user_profile`` — the **identity-owned** ``USER.md`` — how a specific
  human wants to be addressed / worked with (tone, language, preferred
  tools). Identity-editable. One per (workspace, identity).
- ``user_soul`` — ``SOUL.md``: passively accumulated user modelling
  across 12 canonical dimensions (communication style, goals,
  constraints, ...). Writes require **approval** (pending updates are
  held in ``pending_updates_json`` until the identity or an admin acts).
  One per (workspace, identity).

All three are character-capped so injection stays cheap — unbounded
markdown in a system prompt is the usual way 50-token greetings turn
into 2000-token prefixes. Caps are per-kind and configurable via
``MemoryProfile.MAX_CONTENT_CHARS`` below.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class MemoryProfileKind(StrEnum):
    """Discriminator between the three markdown profiles in one table."""

    WORKSPACE_MEMORY = "workspace_memory"
    USER_PROFILE = "user_profile"
    USER_SOUL = "user_soul"


# Per-kind character caps. Chosen to keep every profile cheaply
# injectable — MEMORY.md + USER.md + SOUL.md together stay well under
# 5 KB in the final system prompt even at cap.
MAX_CONTENT_CHARS: dict[MemoryProfileKind, int] = {
    MemoryProfileKind.WORKSPACE_MEMORY: 2200,
    MemoryProfileKind.USER_PROFILE: 1375,
    MemoryProfileKind.USER_SOUL: 2000,
}


# Canonical 12 dimensions of user modelling. Stored as loose keys in
# ``MemoryProfile.soul_dims_json`` so forks can add extra dimensions
# without migrations — the distillation pass just uses whatever keys
# are populated.
SOUL_DIMENSIONS: tuple[str, ...] = (
    "communication_style",
    "domain_expertise",
    "tone_and_register",
    "goals_current",
    "constraints",
    "preferences_tools",
    "preferences_language",
    "cadence",
    "identity_signals",
    "workflow",
    "avoid_list",
    "history_summary",
)


class MemoryProfile(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "memory_profiles"
    __table_args__ = (
        Index(
            "ix_memory_profiles_scope",
            "workspace_id",
            "kind",
            "subject_id",
            unique=True,
        ),
    )

    kind: Mapped[MemoryProfileKind] = mapped_column(String(32), nullable=False, index=True)
    # For ``workspace_memory`` this equals ``workspace_id`` (so the
    # unique index still partitions cleanly); for ``user_profile`` /
    # ``user_soul`` it's the identity id.
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Only populated when subject is an identity — lets us cascade on
    # identity delete without reading ``subject_id`` generically.
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 12-dim map for SOUL profiles; empty for the other two kinds.
    soul_dims_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Pending SOUL updates awaiting approval. Each entry:
    # {"id": "<uuid>", "proposed_content": str, "proposed_dims": {...},
    #  "proposed_at": iso8601, "proposed_by_identity_id": uuid|null,
    #  "source_session_id": uuid|null, "rationale": str}
    pending_updates_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
