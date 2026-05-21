"""Per-run structured artifact (M0.2).

Folds one agent run into a single immutable JSON document so downstream
PRM scoring (M0.3), Curator (M2.x) and Evolver (M3.x) pipelines have a
stable input that survives prompt churn and provider swaps.

Invariants:

* ``run_id`` is unique — repeated capture for the same run is a no-op
  via the unique index. Service-level callers must select-or-skip.
* ``user_text`` is **never** persisted — only its SHA-256 digest. The
  raw string lives in the ``messages`` table (workspace-scoped) and is
  the lineage anchor.
* Each ``turns_json[*]`` entry can carry the original ``message_id``
  back into the ``messages`` table; downstream evaluators must keep
  that pointer intact when summarising / archiving.

Soft-delete + workspace cascade are wired so M0.11's GDPR sweep can
purge artifacts by identity / workspace without touching this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, desc
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SessionArtifact(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "session_artifacts"
    __table_args__ = (
        # Curator / Evolver pull recent artifacts per workspace ordered
        # by completion time; this composite covers the hot path without
        # forcing a sort on the matching rows.
        Index(
            "ix_session_artifacts_ws_finished_at",
            "workspace_id",
            desc("finished_at"),
        ),
        Index(
            "ix_session_artifacts_ws_session",
            "workspace_id",
            "session_id",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    user_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    turns_json: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    injected_skill_pack_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    invoked_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    iteration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # success / error / partial / cancelled — see ``ArtifactOutcome`` enum
    # in ``app.schemas.session_artifact``. Stored as a free-form string
    # column so an offline migration can append new buckets without an
    # alembic dance.
    final_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Populated by M0.3 ARQ judge — ``NULL`` until the judge runs, which
    # is the bucket Curator picks up via ``list_unjudged``.
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    goal_alignment_avg: Mapped[float | None] = mapped_column(Float, nullable=True)

    finished_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
