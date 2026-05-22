"""Run-quality judge service (M0.3).

Owns the single mutation that turns a parsed :class:`JudgeVerdict` into
two coupled writes — the ``judge_verdicts`` row (full reasoning + the
process notes Curator/Evolver want) and the mirrored
``session_artifacts.judge_score`` float (so list endpoints don't
need a join). Both happen in one transaction.

The aux call itself lives in :mod:`app.agents.auxiliary_client`. This
module is the boundary between "we have a verdict from the LLM" and
"the database knows the run was scored".
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.judge_verdict import JudgeVerdict
from app.db.models.session_artifact import SessionArtifact
from app.repositories.judge_verdict import JudgeVerdictRepository
from app.services import session_artifact as artifact_svc

log = logging.getLogger(__name__)


# Fixed mapping from verdict.score → judge_score float so the column
# stays a 3-valued discrete signal (-1.0 / 0.0 / 1.0). The aux LLM is
# constrained by Pydantic to {-1, 0, 1} but a defensive cast keeps a
# misbehaving model from poisoning the column.
_VALID_SCORES = (-1, 0, 1)


def _normalise_score(score: int) -> int:
    s = int(score)
    if s not in _VALID_SCORES:
        raise ValueError(f"score must be -1/0/1, got {s!r}")
    return s


async def persist_verdict(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    score: int,
    confidence: float,
    rationale: str,
    process_notes: list[str] | None = None,
    error_kind_hint: str | None = None,
    judged_by_model: str | None = None,
    latency_ms: int | None = None,
    degraded: bool = False,
) -> JudgeVerdict:
    """Atomic upsert: write the verdict row + mirror score onto the artifact.

    Idempotent on ``artifact_id`` (the unique index makes a re-judge
    overwrite the prior row instead of accumulating history; the audit
    feed is the historical record).
    """
    s = _normalise_score(score)

    artifact = await artifact_svc.get_artifact_by_id(
        db, workspace_id=workspace_id, artifact_id=artifact_id
    )
    if artifact is None:  # pragma: no cover - get_artifact_by_id raises
        raise NotFound("artifact not found", code="session_artifact.not_found")

    notes = list(process_notes or [])[:5]
    notes = [str(n)[:500] for n in notes]
    rationale_clean = (rationale or "").strip()[:600]
    error_hint_clean = None
    if error_kind_hint:
        error_hint_clean = str(error_kind_hint)[:80] or None

    verdict = await JudgeVerdictRepository(db).upsert_for_artifact(
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        values={
            "score": s,
            "confidence": float(max(0.0, min(1.0, confidence))),
            "rationale": rationale_clean,
            "process_notes_json": notes,
            "error_kind_hint": error_hint_clean,
            "judged_by_model": (judged_by_model or None),
            "latency_ms": (int(latency_ms) if latency_ms is not None else None),
            "degraded": bool(degraded),
        },
    )

    await artifact_svc.update_judge_score(
        db,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        judge_score=float(s),
    )
    return verdict


async def clear_verdict(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> bool:
    """Delete the verdict row and reset ``judge_score`` to ``NULL``.

    Used by ``/rejudge`` so the artifact reverts to the "queued"
    bucket while ARQ re-runs the aux call.
    """
    deleted = await JudgeVerdictRepository(db).delete_for_artifact(
        workspace_id=workspace_id, artifact_id=artifact_id
    )
    artifact = await artifact_svc.get_artifact_by_id(
        db, workspace_id=workspace_id, artifact_id=artifact_id
    )
    artifact.judge_score = None
    await db.flush([artifact])
    return deleted


async def get_verdict(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> JudgeVerdict | None:
    return await JudgeVerdictRepository(db).get_by_artifact(
        workspace_id=workspace_id, artifact_id=artifact_id
    )


# ─── Session-level summary (used by the /judge-summary route) ─
async def session_summary(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
) -> dict[str, int]:
    """Counts verdicts in one session by bucket + the unjudged tail."""
    art_stmt = select(SessionArtifact.id, SessionArtifact.judge_score).where(
        SessionArtifact.workspace_id == workspace_id,
        SessionArtifact.session_id == session_id,
        SessionArtifact.deleted_at.is_(None),
    )
    rows: Sequence[tuple[uuid.UUID, float | None]] = (await db.execute(art_stmt)).tuples().all()
    total = len(rows)
    unjudged = sum(1 for _aid, s in rows if s is None)
    success = sum(1 for _aid, s in rows if s is not None and s >= 0.5)
    failure = sum(1 for _aid, s in rows if s is not None and s <= -0.5)
    partial = total - unjudged - success - failure

    artifact_ids = [aid for aid, _s in rows]
    if artifact_ids:
        verdicts = await JudgeVerdictRepository(db).list_for_artifacts(
            workspace_id=workspace_id, artifact_ids=artifact_ids
        )
        degraded = sum(1 for v in verdicts if v.degraded)
    else:
        degraded = 0

    return {
        "total_artifacts": total,
        "success": success,
        "partial": partial,
        "failure": failure,
        "unjudged": unjudged,
        "degraded": degraded,
    }


async def request_rejudge(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> SessionArtifact:
    """Drop the existing verdict and let the ARQ task re-score.

    Returns the artifact so the caller can render the now-pending row
    immediately. Audit row is written by the API layer (so the request
    object is available for the IP / user-agent fields).
    """
    artifact = await artifact_svc.get_artifact_by_id(
        db, workspace_id=workspace_id, artifact_id=artifact_id
    )
    await JudgeVerdictRepository(db).delete_for_artifact(
        workspace_id=workspace_id, artifact_id=artifact_id
    )
    artifact.judge_score = None
    await db.flush([artifact])
    return artifact


__all__ = [
    "clear_verdict",
    "get_verdict",
    "persist_verdict",
    "request_rejudge",
    "session_summary",
]
