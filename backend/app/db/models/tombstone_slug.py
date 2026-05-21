"""Tombstoned skill-pack slugs (M1.1).

A tombstone is the terminal state for a SkillPack: the row stays in
``skill_packs`` (we never hard-delete a concept) but the slug also
gets pinned here so the workspace cannot recreate a pack with the
same slug. This row exists permanently — retention sweep
(``services/retention.py``) intentionally does not list this table
in ``CASCADE_TARGETS``, by design (roadmap principle 10: "tombstone
also retains slug + content_hash for audit").

The retained ``last_content_hash`` lets a future audit reconstruct
"the deleted pack with this slug had body sha=X at the moment of
tombstoning" without leaking the body itself.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class TombstoneSlug(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "tombstone_slugs"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_tombstone_slugs_ws_slug"),)

    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    original_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
