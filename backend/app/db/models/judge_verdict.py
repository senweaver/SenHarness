"""M0.3 — per-artifact LLM judge verdict.

A verdict is the immutable byproduct of one async judge run: the score
the aux LLM picked (1 / 0 / -1), why, and a few process notes the
reviewer flagged for Curator/Evolver to mine. The score is also
mirrored onto ``session_artifacts.judge_score`` (single SQL transaction
with the verdict insert) so list endpoints don't need a join.

Invariants:

* One verdict per artifact — re-judge replaces the row (idempotent
  upsert by ``artifact_id``). The historical trail lives in
  ``audit_events`` (``judge.completed`` / ``judge.rejudge_requested``)
  rather than on this table.
* ``degraded=True`` rows are written when the workspace breaker is open
  — the score is forced to ``0`` and ``judged_by_model`` is left
  ``NULL`` so the downstream Curator can skip them in training data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class JudgeVerdict(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "judge_verdicts"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    process_notes_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    error_kind_hint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    judged_by_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    degraded: Mapped[bool] = mapped_column(default=False, server_default="false")
