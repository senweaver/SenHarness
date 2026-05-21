"""Auto-verifier for PROPOSED SkillPackVersion rows (M2.4).

Implements the "judge-replay" verification step that promotes a
``state=PROPOSED`` candidate version through the
``VALIDATING → ACCEPTED | REJECTED`` edge of the
:class:`~app.db.models.skill_pack_version.SkillPackVersionState` machine.

Roadmap principle 8 (evolution + online requests are decoupled) shapes
two operating decisions:

* The verifier never re-runs a real agent. Replaying every artifact
  through a fresh agent run would burn an order-of-magnitude more aux
  budget than the propose pipeline itself spends. Instead, each pulled
  ``SessionArtifact`` is re-judged twice — once with the *old* SKILL
  body in the system prompt window and once with the *new* body — and
  the delta of the two judge means is what gates the promotion. The
  judge is the same auxiliary tier M0.3 already trusts; this is the
  cheapest signal that still lets the verifier reject regressions.
* Verification runs on a polling sweep (M2.4 ARQ cron, 30 min cadence)
  rather than a synchronous hook on the M2.7 propose path. Propose
  verbs stay fast and the verifier is allowed to be slow / batchy /
  retry-friendly; a verifier outage cannot back-pressure the agent
  loop.

State machine for one verification run::

    PROPOSED  ─→  VALIDATING  ─→  ACCEPTED            ← delta ≥ threshold
                       │              ↑
                       │              └ skipped_insufficient
                       └→  REJECTED   ← delta < threshold
                       └→  REJECTED   ← errored (aux unavailable / raised)

``ACCEPTED`` does **not** auto-activate; M2.5 approval pipeline owns
the ACCEPTED → ACTIVE edge so a workspace admin still has the final
say. ``skipped_insufficient`` is the "not enough relevant artifacts"
escape hatch — counted as ACCEPTED so the version is unblocked, but
``validation_results.skipped`` is set so the M2.5 UI can flag the
proposal as un-validated for the reviewer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auxiliary_client import (
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
)
from app.core.errors import AppError, NotFound
from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.models.skill_pack_version import (
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.skills import SkillPack
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc
from app.services import skill_version as skill_version_svc
from app.services.evolver_config import get_workspace_evolver_config

log = logging.getLogger(__name__)

__all__ = [
    "ArtifactReplayPair",
    "VerificationResult",
    "VerifierAlreadyTerminal",
    "VerifierBreakerBucket",
    "find_relevant_artifacts",
    "replay_judge_with_skill_swap",
    "verify_skill_version",
]


VerifierBreakerBucket = "verifier"


# ── Errors ───────────────────────────────────────────────────
class VerifierAlreadyTerminal(AppError):  # noqa: N818
    """Caller asked to verify a version that's past PROPOSED.

    Distinct from the lower-level
    :class:`~app.services.skill_version.SkillPackVersionTransitionError`
    so the verify-now endpoint can map it cleanly to a 409 with a
    verifier-specific code.
    """

    code = "skill_verifier.invalid_state"
    default_status = 409


# ── Result shapes ────────────────────────────────────────────
VerificationStatus = Literal[
    "accepted",
    "rejected",
    "skipped_insufficient",
    "errored",
]


@dataclass(slots=True)
class ArtifactReplayPair:
    """One artifact's old-vs-new judge scores.

    ``old_score`` and ``new_score`` are each in ``{-1, 0, 1}`` —
    matching the :class:`~app.agents.auxiliary_client.JudgeVerdict`
    discrete ladder. ``failed=True`` means the underlying aux call
    raised or returned junk; the scorer reports zero for both sides
    so the average isn't poisoned by an aborted call.
    """

    artifact_id: uuid.UUID
    old_score: int
    new_score: int
    failed: bool = False


@dataclass(slots=True)
class VerificationResult:
    version_id: uuid.UUID
    status: VerificationStatus
    old_score_avg: float | None
    new_score_avg: float | None
    score_delta: float | None
    replayed_artifacts: int
    threshold: float
    duration_ms: int
    error: str | None = None
    skipped_replay_pairs: list[ArtifactReplayPair] = field(default_factory=list)


# ── Replay-judge schema ──────────────────────────────────────
class _ReplayScore(BaseModel):
    """Compact aux response for one replay variant.

    The verifier only needs the discrete score back; rationale is
    optional and capped tight so a chatty model can't blow up the
    payload size on the per-version row.
    """

    score: Literal[1, 0, -1]
    rationale: str = Field(default="", max_length=240)


_REPLAY_SYSTEM_PROMPT = (
    "You are a quality auditor replaying a previously completed agent run. "
    "You are given the ORIGINAL trace plus the CONTENT of one SKILL pack as "
    "it would have been injected into the system prompt at run time. Decide "
    "whether the run, *as written*, would have been a clear success "
    "(score=1), partial / acceptable but flawed (score=0), or a failure "
    "(score=-1) had the agent followed the SKILL exactly. Output strict "
    'JSON: {"score": -1|0|1, "rationale": short single sentence}. The '
    "trace itself is fixed; you are scoring whether the SKILL content given "
    "below is the right pairing for that trace."
)


_TURNS_REPLAY_BUDGET = 8000  # one-shot cap so a fat run doesn't blow up the prompt


def _safe_json_dump_turns(turns: list[dict[str, Any]], *, max_chars: int) -> str:
    """JSON dump truncated to ``max_chars``.

    Plain :func:`json.dumps` would expand non-ASCII to ``\\uXXXX``
    sequences and waste budget; ``ensure_ascii=False`` keeps the
    multibyte payload close to the source size. The hard cap protects
    against a runaway aggregator that produced a 200 KB transcript.
    """
    if not turns:
        return "[]"
    try:
        rendered = json.dumps(turns, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        rendered = str(turns)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - len("\n[truncated]")] + "\n[truncated]"


def _render_replay_user_prompt(
    *,
    pack_slug: str,
    skill_content: str | None,
    artifact: SessionArtifact,
    turns_payload: str,
    variant_label: str,
) -> str:
    """Stitch the user prompt for one replay variant.

    ``skill_content=None`` means the original run had no SKILL of this
    slug active (verifier baseline for first-version proposals). The
    explicit empty marker keeps the prompt well-defined instead of
    silently evaluating a half-formed template.
    """
    body = (skill_content or "").strip()
    if not body:
        body = "(no SKILL pack of this slug was active)"
    header = (
        f"final_outcome={artifact.final_outcome} "
        f"error_kind={artifact.error_kind or 'none'} "
        f"iterations={artifact.iteration_count} "
        f"tools={','.join(artifact.invoked_tools or [])}"
    )
    return (
        f"VARIANT={variant_label}\n"
        f"SKILL_SLUG={pack_slug}\n"
        f"SKILL_CONTENT (markdown):\n---\n{body}\n---\n"
        f"RUN_HEADER:\n{header}\n"
        f"RUN_TRACE (json turns):\n{turns_payload}\n"
        "Score the run with the SKILL_CONTENT above as the active prompt."
    )


# ── Find relevant artifacts ─────────────────────────────────
async def find_relevant_artifacts(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    pack_slug: str,
    limit: int = 20,
) -> Sequence[SessionArtifact]:
    """Return the recent artifacts that *could* have benefited from this pack.

    Three heuristics, OR'd together, ordered by ``finished_at DESC``:

    * ``injected_skill_pack_ids`` JSONB array contains the pack id
      (the strongest signal — runtime confirmed the pack was loaded).
    * ``invoked_tools`` JSONB array contains the slug (the agent
      actually called a tool whose name matches the skill).
    * ``turns_json::text ILIKE '%<slug>%'`` — last-resort textual
      hit so a brand-new skill that hasn't been bound yet still gets
      *some* baseline by matching the slug appearing in user
      questions or tool calls. This is the noisy lane, kept because
      first-version proposals have no injection history to anchor on.

    Cross-workspace isolation is enforced by the
    ``workspace_id`` predicate; soft-deleted artifacts are excluded.
    The matching itself runs in Python over a recent-history window so
    the verifier doesn't need a JSONB-search index path that varies by
    Postgres minor version.
    """
    # Pull a generous window of recent artifacts and filter in Python.
    # The window is sized to ``limit * 50`` to give us enough headroom
    # in workspaces where most recent runs don't reference this pack;
    # the loop below short-circuits as soon as ``limit`` selections
    # land so the worst case is one extra query, not a Python-side
    # scan of every artifact ever recorded.
    window_size = max(limit * 50, 200)
    stmt = (
        select(SessionArtifact)
        .where(
            SessionArtifact.workspace_id == workspace_id,
            SessionArtifact.deleted_at.is_(None),
        )
        .order_by(desc(SessionArtifact.finished_at))
        .limit(window_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    selected: list[SessionArtifact] = []
    pack_str = str(pack_id)
    needle = pack_slug.lower() if pack_slug else ""
    for row in rows:
        injected_match = pack_str in [
            str(p) for p in (row.injected_skill_pack_ids or [])
        ]
        tool_match = bool(pack_slug) and pack_slug in (row.invoked_tools or [])
        if injected_match or tool_match:
            selected.append(row)
        elif needle:
            try:
                turns_text = json.dumps(
                    row.turns_json or [], ensure_ascii=False, default=str
                ).lower()
            except (TypeError, ValueError):  # pragma: no cover - defensive
                turns_text = str(row.turns_json or "").lower()
            if needle in turns_text:
                selected.append(row)
        if len(selected) >= limit:
            break

    return selected[:limit]


# ── Replay one artifact ─────────────────────────────────────
async def replay_judge_with_skill_swap(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact: SessionArtifact,
    pack_slug: str,
    old_content: str | None,
    new_content: str,
    timeout_s: float = 25.0,
) -> ArtifactReplayPair:
    """Score the artifact twice: once under the old SKILL body, once under the new.

    Two aux LLM invocations per artifact. Returns ``(0, 0,
    failed=True)`` when either call returns junk or raises — the
    scorer never propagates the aux exception so a single bad
    artifact can't abort the per-version sweep.
    """
    config = await get_aux_model(
        db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW
    )
    if config is None:
        # Fall back to the judge tier — workspaces wired for M0.3 always
        # have a judge model resolved, so this keeps the verifier usable
        # before the operator picks an explicit ``aux_model_skill_review``.
        config = await get_aux_model(
            db, workspace_id=workspace_id, task=AuxiliaryTask.JUDGE
        )
    if config is None:
        return ArtifactReplayPair(
            artifact_id=artifact.id, old_score=0, new_score=0, failed=True
        )

    turns_payload = _safe_json_dump_turns(
        list(artifact.turns_json or []), max_chars=_TURNS_REPLAY_BUDGET
    )

    async def _one(content: str | None, label: str) -> int:
        prompt = _render_replay_user_prompt(
            pack_slug=pack_slug,
            skill_content=content,
            artifact=artifact,
            turns_payload=turns_payload,
            variant_label=label,
        )
        try:
            response = await call_aux_chat(
                config=config,
                system=_REPLAY_SYSTEM_PROMPT,
                user=prompt,
                response_format=_ReplayScore,
                timeout_s=timeout_s,
            )
        except Exception:
            log.exception(
                "verifier replay aux call raised for artifact=%s variant=%s",
                artifact.id,
                label,
            )
            return None  # type: ignore[return-value]
        if isinstance(response, _ReplayScore):
            return int(response.score)
        return None  # type: ignore[return-value]

    try:
        old, new = await asyncio.gather(
            _one(old_content, "old"), _one(new_content, "new")
        )
    except Exception:
        log.exception(
            "verifier replay gather crashed for artifact=%s", artifact.id
        )
        return ArtifactReplayPair(
            artifact_id=artifact.id, old_score=0, new_score=0, failed=True
        )

    if old is None or new is None:
        return ArtifactReplayPair(
            artifact_id=artifact.id, old_score=0, new_score=0, failed=True
        )

    return ArtifactReplayPair(
        artifact_id=artifact.id, old_score=int(old), new_score=int(new)
    )


# ── Verify one PROPOSED version ─────────────────────────────
async def _load_pack_or_raise(
    db: AsyncSession, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
) -> SkillPack:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return pack


async def _load_proposed_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
) -> SkillPackVersion:
    version = await SkillPackVersionRepository(db).get(version_id)
    if version is None or version.workspace_id != workspace_id:
        raise NotFound(
            "skill_pack_version_not_found",
            code="skill_version.not_found",
        )
    if version.state != SkillPackVersionState.PROPOSED:
        raise VerifierAlreadyTerminal(
            f"version state is {version.state.value}, expected proposed",
            code="skill_verifier.invalid_state",
            extras={
                "version_id": str(version_id),
                "current_state": version.state.value,
            },
        )
    return version


async def _persist_validation_results(
    db: AsyncSession, *, version: SkillPackVersion, payload: dict[str, Any]
) -> None:
    """Replace the version's ``validation_results`` JSONB with ``payload``."""
    version.validation_results = payload
    await db.flush([version])


async def _audit_verifier(
    db: AsyncSession,
    *,
    action: str,
    workspace_id: uuid.UUID,
    version: SkillPackVersion | None,
    summary: str,
    metadata: dict[str, Any] | None = None,
    request: Any = None,
) -> None:
    await audit_svc.record(
        db,
        action=action,
        actor_identity_id=None,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=version.id if version is not None else None,
        summary=summary,
        metadata=metadata or {},
        request=request,
    )


def _build_validation_results(
    *,
    pairs: Sequence[ArtifactReplayPair],
    old_avg: float | None,
    new_avg: float | None,
    delta: float | None,
    threshold: float,
    status: VerificationStatus,
    started_at: datetime,
    duration_ms: int,
    error: str | None,
    artifacts_examined: int,
) -> dict[str, Any]:
    return {
        "status": status,
        "threshold": threshold,
        "score_delta": delta,
        "old_score_avg": old_avg,
        "new_score_avg": new_avg,
        "replayed_artifacts": len(pairs),
        "artifacts_examined": artifacts_examined,
        "skipped": status == "skipped_insufficient",
        "errored": status == "errored",
        "error": error,
        "started_at": started_at.isoformat(),
        "duration_ms": int(duration_ms),
        "pairs": [
            {
                "artifact_id": str(p.artifact_id),
                "old_score": int(p.old_score),
                "new_score": int(p.new_score),
                "failed": bool(p.failed),
            }
            for p in pairs
        ],
    }


async def verify_skill_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Any = None,
) -> VerificationResult:
    """Run one verification pass for a single PROPOSED version.

    Caller is responsible for committing the surrounding transaction.
    The function flushes its own state-machine transitions through
    :func:`app.services.skill_version.transition_version` so the
    audit / state-changed timestamps land in the same unit of work.
    """
    started = utcnow_naive()
    monotonic_started = time.monotonic()

    version = await _load_proposed_version(
        db, workspace_id=workspace_id, version_id=version_id
    )
    pack = await _load_pack_or_raise(
        db, workspace_id=workspace_id, pack_id=version.pack_id
    )
    config = await get_workspace_evolver_config(db, workspace_id=workspace_id)
    threshold = float(config.auto_verifier.min_score_delta)
    min_artifacts = int(config.auto_verifier.min_replay_artifacts)

    await _audit_verifier(
        db,
        action="verifier.started",
        workspace_id=workspace_id,
        version=version,
        summary=f"verifier started for v{version.version_no}",
        metadata={
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "version_id": str(version.id),
            "version_no": int(version.version_no),
            "threshold": threshold,
            "min_replay_artifacts": min_artifacts,
        },
        request=request,
    )

    await skill_version_svc.transition_version(
        db,
        workspace_id=workspace_id,
        version_id=version.id,
        target_state=SkillPackVersionState.VALIDATING,
        actor_identity_id=None,
        reason="auto verifier started",
        request=request,
    )

    try:
        artifacts = await find_relevant_artifacts(
            db,
            workspace_id=workspace_id,
            pack_id=pack.id,
            pack_slug=pack.slug,
            limit=max(min_artifacts * 2, min_artifacts),
        )

        if len(artifacts) < min_artifacts:
            duration_ms = int((time.monotonic() - monotonic_started) * 1000)
            results_payload = _build_validation_results(
                pairs=[],
                old_avg=None,
                new_avg=None,
                delta=None,
                threshold=threshold,
                status="skipped_insufficient",
                started_at=started,
                duration_ms=duration_ms,
                error=None,
                artifacts_examined=len(artifacts),
            )
            await _persist_validation_results(db, version=version, payload=results_payload)
            await skill_version_svc.transition_version(
                db,
                workspace_id=workspace_id,
                version_id=version.id,
                target_state=SkillPackVersionState.ACCEPTED,
                actor_identity_id=None,
                reason="auto verifier skipped: insufficient artifacts",
                request=request,
            )
            await _audit_verifier(
                db,
                action="verifier.skipped_insufficient",
                workspace_id=workspace_id,
                version=version,
                summary=(
                    f"verifier skipped v{version.version_no}: "
                    f"{len(artifacts)} artifacts < min {min_artifacts}"
                ),
                metadata={
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "version_no": int(version.version_no),
                    "artifacts_examined": len(artifacts),
                    "min_replay_artifacts": min_artifacts,
                },
                request=request,
            )
            await _audit_verifier(
                db,
                action="verifier.completed",
                workspace_id=workspace_id,
                version=version,
                summary=(
                    f"verifier accepted v{version.version_no} "
                    "(skipped_insufficient)"
                ),
                metadata={
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "version_no": int(version.version_no),
                    "status": "skipped_insufficient",
                    "old_score_avg": None,
                    "new_score_avg": None,
                    "delta": None,
                    "threshold": threshold,
                    "replayed_count": 0,
                    "duration_ms": duration_ms,
                },
                request=request,
            )
            return VerificationResult(
                version_id=version.id,
                status="skipped_insufficient",
                old_score_avg=None,
                new_score_avg=None,
                score_delta=None,
                replayed_artifacts=0,
                threshold=threshold,
                duration_ms=duration_ms,
            )

        baseline = await SkillPackVersionRepository(db).get_active(
            workspace_id=workspace_id, pack_id=pack.id
        )
        old_content = baseline.content_md if baseline is not None else None
        new_content = version.content_md or ""

        pairs = await asyncio.gather(
            *(
                replay_judge_with_skill_swap(
                    db,
                    workspace_id=workspace_id,
                    artifact=art,
                    pack_slug=pack.slug,
                    old_content=old_content,
                    new_content=new_content,
                )
                for art in artifacts
            )
        )

        old_avg = sum(p.old_score for p in pairs) / float(len(pairs))
        new_avg = sum(p.new_score for p in pairs) / float(len(pairs))
        delta = new_avg - old_avg

        if delta >= threshold:
            target_state = SkillPackVersionState.ACCEPTED
            status: VerificationStatus = "accepted"
            transition_reason = "auto verifier accepted: delta >= threshold"
        else:
            target_state = SkillPackVersionState.REJECTED
            status = "rejected"
            transition_reason = "auto verifier rejected: delta < threshold"

        duration_ms = int((time.monotonic() - monotonic_started) * 1000)
        results_payload = _build_validation_results(
            pairs=pairs,
            old_avg=old_avg,
            new_avg=new_avg,
            delta=delta,
            threshold=threshold,
            status=status,
            started_at=started,
            duration_ms=duration_ms,
            error=None,
            artifacts_examined=len(artifacts),
        )
        await _persist_validation_results(db, version=version, payload=results_payload)
        version.judge_score = float(new_avg)
        await db.flush([version])

        await skill_version_svc.transition_version(
            db,
            workspace_id=workspace_id,
            version_id=version.id,
            target_state=target_state,
            actor_identity_id=None,
            reason=transition_reason,
            request=request,
        )
        await _audit_verifier(
            db,
            action="verifier.completed",
            workspace_id=workspace_id,
            version=version,
            summary=(
                f"verifier {status} v{version.version_no} "
                f"(delta={delta:.3f}, threshold={threshold:.3f})"
            ),
            metadata={
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "version_no": int(version.version_no),
                "status": status,
                "old_score_avg": old_avg,
                "new_score_avg": new_avg,
                "delta": delta,
                "threshold": threshold,
                "replayed_count": len(pairs),
                "duration_ms": duration_ms,
            },
            request=request,
        )

        return VerificationResult(
            version_id=version.id,
            status=status,
            old_score_avg=old_avg,
            new_score_avg=new_avg,
            score_delta=delta,
            replayed_artifacts=len(pairs),
            threshold=threshold,
            duration_ms=duration_ms,
            skipped_replay_pairs=list(pairs),
        )

    except Exception as exc:
        # Catastrophic path — usually an aux LLM tier blow-up that
        # didn't degrade gracefully inside ``replay_judge_with_skill_swap``.
        # Fail the version so the agent can't slide a half-validated
        # candidate into the approval queue, and surface the error
        # both in the validation_results blob and the audit feed.
        log.exception(
            "verifier failed catastrophically for version=%s pack=%s",
            version.id,
            pack.id,
        )
        duration_ms = int((time.monotonic() - monotonic_started) * 1000)
        results_payload = _build_validation_results(
            pairs=[],
            old_avg=None,
            new_avg=None,
            delta=None,
            threshold=threshold,
            status="errored",
            started_at=started,
            duration_ms=duration_ms,
            error=f"{type(exc).__name__}: {exc}"[:240],
            artifacts_examined=0,
        )
        try:
            await _persist_validation_results(
                db, version=version, payload=results_payload
            )
        except Exception:  # pragma: no cover
            log.exception(
                "verifier failed to persist errored results for version=%s",
                version.id,
            )
        try:
            current = (
                await SkillPackVersionRepository(db).get(version.id)
            )
            if (
                current is not None
                and current.state == SkillPackVersionState.VALIDATING
            ):
                await skill_version_svc.transition_version(
                    db,
                    workspace_id=workspace_id,
                    version_id=version.id,
                    target_state=SkillPackVersionState.REJECTED,
                    actor_identity_id=None,
                    reason="auto verifier errored",
                    request=request,
                )
        except Exception:  # pragma: no cover
            log.exception(
                "verifier failed to transition errored version=%s to rejected",
                version.id,
            )
        await _audit_verifier(
            db,
            action="verifier.errored",
            workspace_id=workspace_id,
            version=version,
            summary=f"verifier errored for v{version.version_no}",
            metadata={
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "version_no": int(version.version_no),
                "error": f"{type(exc).__name__}: {exc}"[:240],
                "duration_ms": duration_ms,
            },
            request=request,
        )
        return VerificationResult(
            version_id=version.id,
            status="errored",
            old_score_avg=None,
            new_score_avg=None,
            score_delta=None,
            replayed_artifacts=0,
            threshold=threshold,
            duration_ms=duration_ms,
            error=f"{type(exc).__name__}: {exc}"[:240],
        )
