"""Per-identity Honcho-style 12-dimension dialectic user model (M3.7).

One row per ``(workspace, identity, dimension, version)`` capturing one
fact the aux LLM extracted from recent runs. The active fact for a
dimension is the latest non-rejected, non-superseded row that either:

* carries ``user_confirmed=True`` (always injected when active), or
* carries ``confidence >= 0.7`` (auto-injected even without confirmation).

When the user hits *Reject*, the row's ``user_rejected`` flips to
``True`` and stays there forever — the renderer uses that as the
"never inject this candidate again" signal regardless of confidence
or future supersedes. *Confirm* flips ``user_confirmed=True`` so a
low-confidence candidate the user vouched for keeps being injected.

Workspace-scoped + soft-delete so the M0.11 GDPR cascade hits the row
through ``CASCADE_TARGETS``; the existing ``inspect.has_table`` guard
keeps the entry inert on pre-0056 deployments.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class UserProfileDimension(StrEnum):
    """The 12 dimensions the dialectic extractor maintains.

    Order matches the roadmap (M3.7). Adding a new dimension requires
    a migration only when a new in-database default needs to ship —
    the ``Enum(..., native_enum=False)`` column already accepts any
    value the StrEnum exposes.
    """

    COMMUNICATION_STYLE = "communication_style"
    DOMAIN_EXPERTISE = "domain_expertise"
    DECISION_PREFERENCE = "decision_preference"
    TONE_PREFERENCE = "tone_preference"
    LANGUAGE_PRIMARY = "language_primary"
    WORKING_HOURS = "working_hours"
    AUTONOMY_TOLERANCE = "autonomy_tolerance"
    DETAIL_PREFERENCE = "detail_preference"
    FORMALITY = "formality"
    PROACTIVITY_TOLERANCE = "proactivity_tolerance"
    DOMAIN_INTEREST = "domain_interest"
    GOAL_PATTERN = "goal_pattern"


USER_PROFILE_DIMENSIONS: tuple[UserProfileDimension, ...] = tuple(
    UserProfileDimension
)

# Hard cap on each fact body. The renderer may further trim per-line so the
# always-on memory hard cap (M0.7 = 4000 chars total) is respected, but a
# single-fact upper bound stops a runaway extraction from filling the column.
MAX_FACT_CHARS: int = 500

# Confidence threshold under which a fact stays *pending* — the user must
# confirm it before it gets injected. Locked here so the service, the API
# layer, and the tests share one source of truth.
AUTO_INJECT_CONFIDENCE_THRESHOLD: float = 0.7


class UserProfileFact(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "user_profile_facts"
    __table_args__ = (
        Index(
            "ix_user_profile_facts_identity_dim_active",
            "workspace_id",
            "identity_id",
            "dimension",
        ),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[UserProfileDimension] = mapped_column(
        Enum(UserProfileDimension, native_enum=False, length=64),
        nullable=False,
        index=True,
    )

    fact: Mapped[str] = mapped_column(String(MAX_FACT_CHARS), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_run_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_profile_facts.id", ondelete="SET NULL"),
        nullable=True,
    )

    user_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    user_rejected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


__all__ = [
    "AUTO_INJECT_CONFIDENCE_THRESHOLD",
    "MAX_FACT_CHARS",
    "USER_PROFILE_DIMENSIONS",
    "UserProfileDimension",
    "UserProfileFact",
]
