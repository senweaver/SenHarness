"""Per-event skill usage telemetry (M1.3).

Each row captures one observable event in the lifecycle of a SkillPack
during an agent run: the pack was injected into the run prompt, the
agent read its full body, the agent invoked it inside a tool call, the
runtime patched the body in-flight, or the runtime dropped it because
a context-window cap was hit. The aggregate of these rows powers the
``last_used_at`` and ``effectiveness_avg`` columns on
:class:`~app.db.models.skills.SkillPack`, plus the per-pack admin
"Usage" UI.

Invariants:

* ``contribution_score`` is set asynchronously by the M0.3 judge / the
  M1.4 curator and may stay ``NULL`` indefinitely. Aggregation skips
  ``NULL`` rows so an empty score field never depresses the rolling
  average.
* ``run_id`` is *not* an FK because runs aren't a first-class table —
  the idempotency anchor is the unique pair ``(workspace_id, run_id,
  pack_id, event_kind)`` enforced at the service layer when batching
  inserts. Repeated batch inserts for the same run are tolerated; the
  rollup deduplicates by max(created_at).
* ``version_id`` references :class:`SkillPackVersion` once M1.2 lands.
  Until then the FK is registered conditionally in the migration via
  ``inspect(conn).has_table('skill_pack_versions')``; the column is
  nullable so a row inserted before M1.2 stays valid forever.

Retention: this table participates in the M0.11 cascade through the
``identity_id`` and ``workspace_id`` columns. Soft-delete is
intentionally absent — telemetry rows are short-lived audit artifacts;
the daily physical purge does not apply (no ``deleted_at`` column).
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class SkillUsageEventKind(StrEnum):
    """Five observable events emitted by the runtime per skill pack."""

    INJECTED = "injected"
    READ_FULL = "read_full"
    USED_IN_TOOL = "used_in_tool"
    PATCHED = "patched"
    DROPPED_AT_CAP = "dropped_at_cap"


class SkillUsage(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "skill_usage"
    __table_args__ = (
        # Hot path: per-pack drawer reads recent rows ordered by created_at.
        Index(
            "ix_skill_usage_ws_pack_created",
            "workspace_id",
            "pack_id",
            "created_at",
        ),
        # Reverse lookup for "what packs did this run touch?".
        Index(
            "ix_skill_usage_ws_run",
            "workspace_id",
            "run_id",
        ),
        # Stats endpoint slices by event_kind across a workspace window.
        Index(
            "ix_skill_usage_ws_kind_created",
            "workspace_id",
            "event_kind",
            "created_at",
        ),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_packs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
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
    event_kind: Mapped[SkillUsageEventKind] = mapped_column(
        SAEnum(
            SkillUsageEventKind,
            native_enum=False,
            length=32,
            name="skill_usage_event_kind",
        ),
        nullable=False,
        index=True,
    )
    contribution_score: Mapped[float | None] = mapped_column(Float, nullable=True)
