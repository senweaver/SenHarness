"""M3.7 — Honcho-style 12-dimension dialectic user modeling service.

Pipeline
--------

1. **Extract** (``extract_facts_from_runs``) — pull the most recent
   ``since_run_count`` ``SessionArtifact`` rows for one identity in one
   workspace, ask the aux LLM (task ``SUMMARIZE``) to map them to the
   12 dimensions, and persist the result as ``UserProfileFact`` rows.
   * ``confidence >= AUTO_INJECT_CONFIDENCE_THRESHOLD`` → straight in
     as a candidate that the renderer can auto-inject.
   * ``confidence <  AUTO_INJECT_CONFIDENCE_THRESHOLD`` → still
     persisted (the user can confirm to promote it) but never
     injected unless ``user_confirmed=True``.
   The previous active row for the same dimension is marked
   superseded — the chain stays intact for the dialectic UI.

2. **Render** (``render_facts_for_injection``) — walk the 12
   dimensions, pick the best non-rejected row per dimension, and
   compose a single ``## USER PROFACTS`` block that respects the
   M0.7 always-on hard cap (4000 chars total, one line per dim).

3. **Consent** (``confirm_fact`` / ``reject_fact``) — user-driven
   overrides, audited under ``user_profile.fact_confirmed`` /
   ``user_profile.fact_rejected``. Reject is permanent (the row
   stays at ``user_rejected=True``) which is the design's
   "never inject again" signal.

Audit action keys (verbatim — referenced by tests + UI filters):

* ``user_profile.facts_extracted``
* ``user_profile.fact_confirmed``
* ``user_profile.fact_rejected``
* ``user_profile.fact_superseded``
* ``user_profile.injection_rendered``
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
)
from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.db.models.identity import Identity
from app.db.models.session_artifact import SessionArtifact
from app.db.models.user_profile import (
    AUTO_INJECT_CONFIDENCE_THRESHOLD,
    MAX_FACT_CHARS,
    UserProfileDimension,
    UserProfileFact,
)
from app.jobs._breaker import is_breaker_open
from app.repositories.user_profile import UserProfileFactRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


# ─── Audit action constants ──────────────────────────────────
AUDIT_FACTS_EXTRACTED = "user_profile.facts_extracted"
AUDIT_FACT_CONFIRMED = "user_profile.fact_confirmed"
AUDIT_FACT_REJECTED = "user_profile.fact_rejected"
AUDIT_FACT_SUPERSEDED = "user_profile.fact_superseded"
AUDIT_INJECTION_RENDERED = "user_profile.injection_rendered"


# ─── Tunables ────────────────────────────────────────────────
DEFAULT_SINCE_RUN_COUNT = 10
DEFAULT_RECENT_DAYS = 30
DEFAULT_INJECT_MAX_CHARS = 4000
PER_LINE_MAX_CHARS = 300

# Reuse the evolver breaker bucket so a sick aux LLM trips both
# extractors together without doubling the failure surface (the
# user-modeling extractor never bumps the breaker, only reads it —
# bumping stays the responsibility of the evolver / judge paths).
EVOLVER_BREAKER_BUCKET = "evolver"


__all__ = [
    "AUDIT_FACTS_EXTRACTED",
    "AUDIT_FACT_CONFIRMED",
    "AUDIT_FACT_REJECTED",
    "AUDIT_FACT_SUPERSEDED",
    "AUDIT_INJECTION_RENDERED",
    "AUTO_INJECT_CONFIDENCE_THRESHOLD",
    "DEFAULT_INJECT_MAX_CHARS",
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_SINCE_RUN_COUNT",
    "EVOLVER_BREAKER_BUCKET",
    "ExtractOutcome",
    "confirm_fact",
    "extract_facts_from_runs",
    "list_active_facts",
    "list_active_identities",
    "reject_fact",
    "render_facts_for_injection",
]


# ─── Data carriers ──────────────────────────────────────────
class _DimensionDraft(BaseModel):
    """One bucket the aux LLM returns."""

    dimension: str
    fact: str = Field(max_length=MAX_FACT_CHARS)
    confidence: float = Field(ge=0.0, le=1.0)


class _FactExtractionDraft(BaseModel):
    """Top-level aux output — the 12-dim mapping for one identity."""

    facts: list[_DimensionDraft] = Field(default_factory=list)


@dataclass(slots=True)
class ExtractOutcome:
    """Result carrier for :func:`extract_facts_from_runs`."""

    workspace_id: uuid.UUID
    identity_id: uuid.UUID
    facts_created: int = 0
    facts_superseded: int = 0
    facts_unchanged: int = 0
    artifacts_examined: int = 0
    aux_skipped: bool = False
    aux_skip_reason: str | None = None
    duration_ms: int = 0
    new_fact_ids: list[uuid.UUID] = field(default_factory=list)


# ─── Read paths ──────────────────────────────────────────────
async def list_active_facts(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> dict[UserProfileDimension, UserProfileFact | None]:
    """Active fact per dimension (or ``None`` when nothing eligible)."""
    repo = UserProfileFactRepository(db)
    return await repo.list_active_per_dimension(workspace_id=workspace_id, identity_id=identity_id)


async def render_facts_for_injection(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    max_chars: int = DEFAULT_INJECT_MAX_CHARS,
) -> str:
    """Build the system-prompt fragment for one identity.

    Filter rules (the design contract):

    * ``user_rejected=True`` → never injected.
    * ``user_confirmed=False`` → only injected if
      ``confidence >= AUTO_INJECT_CONFIDENCE_THRESHOLD``.
    * ``user_confirmed=True`` → always injected (any confidence).
    * One bullet per dimension (the highest-confidence eligible row).
    * The composed block is hard-trimmed to ``max_chars``; the per-line
      cap (``PER_LINE_MAX_CHARS``) keeps a single oversized fact from
      stealing budget from the others.

    Returns an empty string when nothing is eligible — callers must
    treat that as "skip injection" rather than emitting an empty
    bullet list. This function never raises; aux / DB failures upstream
    are handled at the call site.
    """
    facts_by_dim = await list_active_facts(db, workspace_id=workspace_id, identity_id=identity_id)

    lines: list[str] = []
    for dim in UserProfileDimension:
        fact = facts_by_dim.get(dim)
        if fact is None:
            continue
        if fact.user_rejected:
            continue
        if not fact.user_confirmed and float(fact.confidence) < AUTO_INJECT_CONFIDENCE_THRESHOLD:
            continue
        body = (fact.fact or "").strip().replace("\n", " ")
        if not body:
            continue
        if len(body) > PER_LINE_MAX_CHARS:
            body = body[: PER_LINE_MAX_CHARS - 1] + "…"
        marker = "✓" if fact.user_confirmed else " "
        lines.append(f"- [{marker}] {dim.value}: {body}")

    if not lines:
        return ""

    header = "## USER FACTS\n"
    body = "\n".join(lines)
    rendered = header + body
    if len(rendered) > max_chars:
        # Reserve one trailing ellipsis line so the trim is visible to
        # an operator reading the log. We chop on a line boundary so
        # the bullet list doesn't end mid-word.
        budget = max(0, max_chars - len("\n[truncated]"))
        clipped: list[str] = []
        running = len(header)
        for line in lines:
            if running + len(line) + 1 > budget:
                break
            clipped.append(line)
            running += len(line) + 1
        rendered = header + "\n".join(clipped) + "\n[truncated]"

    # Best-effort audit — never fails the injection path.
    try:
        await audit_svc.record(
            db,
            action=AUDIT_INJECTION_RENDERED,
            actor_identity_id=None,
            workspace_id=workspace_id,
            resource_type="user_profile",
            resource_id=None,
            summary=(f"user_profile injection rendered: {len(lines)} dims, {len(rendered)} chars"),
            metadata={
                "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
                "dim_count": len(lines),
                "rendered_chars": len(rendered),
                "max_chars": int(max_chars),
            },
        )
    except Exception:  # pragma: no cover - audit is best-effort
        pass

    return rendered


# ─── Extraction ──────────────────────────────────────────────
async def extract_facts_from_runs(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    since_run_count: int = DEFAULT_SINCE_RUN_COUNT,
    invocation_kind: str = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
) -> ExtractOutcome:
    """Aux-LLM extract pass for one ``(workspace, identity)`` pair.

    Walks the ``since_run_count`` most recent non-deleted
    ``SessionArtifact`` rows for the identity, asks the aux LLM to map
    them to the 12 dimensions, persists the deltas, and returns an
    :class:`ExtractOutcome`. Audits one ``user_profile.facts_extracted``
    summary plus a ``user_profile.fact_superseded`` row per superseded
    candidate so the dialectic chain is reconstructable from the audit
    feed alone.
    """
    started = utcnow_naive()
    started_perf = time.perf_counter()
    outcome = ExtractOutcome(
        workspace_id=workspace_id,
        identity_id=identity_id,
    )

    artifacts = await _list_recent_artifacts(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
        limit=int(since_run_count),
    )
    outcome.artifacts_examined = len(artifacts)
    if not artifacts:
        outcome.aux_skipped = True
        outcome.aux_skip_reason = "no_artifacts"
        outcome.duration_ms = int((time.perf_counter() - started_perf) * 1000)
        return outcome

    breaker_open = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=5,
    )
    if breaker_open:
        outcome.aux_skipped = True
        outcome.aux_skip_reason = "breaker_open"
        outcome.duration_ms = int((time.perf_counter() - started_perf) * 1000)
        return outcome

    aux_config = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.SUMMARIZE)
    if aux_config is None:
        outcome.aux_skipped = True
        outcome.aux_skip_reason = "no_aux_model"
        outcome.duration_ms = int((time.perf_counter() - started_perf) * 1000)
        return outcome

    draft = await _extract_with_aux(config=aux_config, artifacts=artifacts)
    if draft is None:
        outcome.aux_skipped = True
        outcome.aux_skip_reason = "aux_failure"
        outcome.duration_ms = int((time.perf_counter() - started_perf) * 1000)
        return outcome

    repo = UserProfileFactRepository(db)
    valid_dims = {d.value: d for d in UserProfileDimension}
    source_run_ids = [str(a.run_id) for a in artifacts]

    for entry in draft.facts:
        dim = valid_dims.get(entry.dimension.strip().lower())
        if dim is None:
            continue
        new_text = (entry.fact or "").strip()
        if not new_text:
            continue
        if len(new_text) > MAX_FACT_CHARS:
            new_text = new_text[: MAX_FACT_CHARS - 1] + "…"
        confidence = max(0.0, min(1.0, float(entry.confidence)))

        prior = await repo.get_active_for_dimension(
            workspace_id=workspace_id,
            identity_id=identity_id,
            dimension=dim,
        )
        if prior is not None and (prior.fact or "").strip() == new_text:
            outcome.facts_unchanged += 1
            continue

        new_fact = await repo.create(
            workspace_id=workspace_id,
            identity_id=identity_id,
            dimension=dim,
            fact=new_text,
            confidence=confidence,
            source_run_ids=source_run_ids,
            superseded_by_id=None,
            user_confirmed=False,
            user_rejected=False,
        )
        outcome.facts_created += 1
        outcome.new_fact_ids.append(new_fact.id)

        if prior is not None:
            await repo.supersede(fact_id=prior.id, by_fact_id=new_fact.id)
            outcome.facts_superseded += 1
            await audit_svc.record(
                db,
                action=AUDIT_FACT_SUPERSEDED,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="user_profile",
                resource_id=prior.id,
                summary=(f"user_profile {dim.value} superseded by extraction"),
                metadata={
                    "dimension": dim.value,
                    "old_fact_id": str(prior.id),
                    "new_fact_id": str(new_fact.id),
                    "old_confidence": float(prior.confidence),
                    "new_confidence": confidence,
                    "invocation_kind": invocation_kind,
                },
            )

    outcome.duration_ms = int((time.perf_counter() - started_perf) * 1000)

    await audit_svc.record(
        db,
        action=AUDIT_FACTS_EXTRACTED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="user_profile",
        resource_id=None,
        summary=(
            f"user_profile extraction ({invocation_kind}): "
            f"{outcome.facts_created} new, "
            f"{outcome.facts_superseded} superseded, "
            f"{outcome.facts_unchanged} unchanged"
        ),
        metadata={
            "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
            "invocation_kind": invocation_kind,
            "since_run_count": int(since_run_count),
            "artifacts_examined": int(outcome.artifacts_examined),
            "facts_created": int(outcome.facts_created),
            "facts_superseded": int(outcome.facts_superseded),
            "facts_unchanged": int(outcome.facts_unchanged),
            "duration_ms": int(outcome.duration_ms),
            "started_at": started.isoformat(),
        },
    )

    return outcome


async def _list_recent_artifacts(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    limit: int,
) -> Sequence[SessionArtifact]:
    stmt = (
        select(SessionArtifact)
        .where(
            SessionArtifact.workspace_id == workspace_id,
            SessionArtifact.identity_id == identity_id,
            SessionArtifact.deleted_at.is_(None),
        )
        .order_by(SessionArtifact.finished_at.desc())
        .limit(int(limit))
    )
    return list((await db.execute(stmt)).scalars().all())


_EXTRACT_SYSTEM_PROMPT = (
    "You build a 12-dimension user model from recent agent run "
    "transcripts. The dimensions are: communication_style, "
    "domain_expertise, decision_preference, tone_preference, "
    "language_primary, working_hours, autonomy_tolerance, "
    "detail_preference, formality, proactivity_tolerance, "
    "domain_interest, goal_pattern. For each dimension you can "
    "support with at least one concrete observation, return one "
    "short factual sentence (≤200 chars) and a confidence between "
    "0.0 and 1.0. Skip a dimension entirely when the runs do not "
    "support it; do NOT fabricate. Confidence ≥ 0.7 means 'I would "
    "stake the reputation of the system on this'. Reply as JSON "
    'matching: {"facts":[{"dimension":"...","fact":"...",'
    '"confidence":0.0}]}. Use only the listed dimension keys.'
)


def _build_extract_user_prompt(artifacts: Sequence[SessionArtifact]) -> str:
    parts: list[str] = [f"Artifacts: {len(artifacts)}"]
    for idx, art in enumerate(artifacts[:30]):
        turns = art.turns_json or []
        user_lines: list[str] = []
        assistant_lines: list[str] = []
        for turn in turns[:20]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").lower()
            text = str(turn.get("text") or "").strip().replace("\n", " ")
            if not text:
                continue
            text = text[:280]
            if role == "user":
                user_lines.append(text)
            elif role == "assistant":
                assistant_lines.append(text)
        block = (
            f"--- run #{idx} outcome={art.final_outcome} "
            f"tools={','.join(art.invoked_tools or [])[:80]}\n"
            f"USER: {' || '.join(user_lines)[:600]}\n"
            f"ASSISTANT: {' || '.join(assistant_lines)[:600]}"
        )
        parts.append(block[:1200])
    return "\n".join(parts)[:9000]


async def _extract_with_aux(
    *,
    config: AuxiliaryConfig,
    artifacts: Sequence[SessionArtifact],
) -> _FactExtractionDraft | None:
    user_prompt = _build_extract_user_prompt(artifacts)
    response = await call_aux_chat(
        config=config,
        system=_EXTRACT_SYSTEM_PROMPT,
        user=user_prompt,
        response_format=_FactExtractionDraft,
        timeout_s=30.0,
    )
    if isinstance(response, _FactExtractionDraft):
        return response
    return None


# ─── Consent paths ───────────────────────────────────────────
async def confirm_fact(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    fact_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> UserProfileFact:
    """User-confirms one row.

    The repo enforces that the row belongs to the caller; we
    additionally verify the workspace_id so a cross-workspace token
    can never confirm someone else's fact.
    """
    repo = UserProfileFactRepository(db)
    fact = await repo.get(fact_id)
    if fact is None or fact.workspace_id != workspace_id:
        raise NotFound(
            "user_profile_fact_not_found",
            code="user_profile.fact_not_found",
        )
    fact = await repo.confirm(fact_id=fact_id, identity_id=identity_id)
    await audit_svc.record(
        db,
        action=AUDIT_FACT_CONFIRMED,
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="user_profile",
        resource_id=fact.id,
        summary=f"user_profile {fact.dimension.value} confirmed",
        metadata={
            "dimension": fact.dimension.value,
            "fact_id": str(fact.id),
            "confidence": float(fact.confidence),
        },
    )
    return fact


async def reject_fact(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    fact_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> UserProfileFact:
    """User-rejects one row. Permanent — never injected again."""
    repo = UserProfileFactRepository(db)
    fact = await repo.get(fact_id)
    if fact is None or fact.workspace_id != workspace_id:
        raise NotFound(
            "user_profile_fact_not_found",
            code="user_profile.fact_not_found",
        )
    fact = await repo.reject(fact_id=fact_id, identity_id=identity_id)
    await audit_svc.record(
        db,
        action=AUDIT_FACT_REJECTED,
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="user_profile",
        resource_id=fact.id,
        summary=f"user_profile {fact.dimension.value} rejected",
        metadata={
            "dimension": fact.dimension.value,
            "fact_id": str(fact.id),
            "confidence": float(fact.confidence),
        },
    )
    return fact


# ─── Sweep helpers ──────────────────────────────────────────
async def list_active_identities(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    since_days: int = DEFAULT_RECENT_DAYS,
) -> list[uuid.UUID]:
    """Distinct identity ids with ≥1 artifact in the lookback window.

    The sweep iterates over this list; identities outside the window
    are skipped because their model would only repeat what the
    previous pass already produced.
    """
    cutoff: datetime = utcnow_naive() - timedelta(days=int(since_days))
    stmt = (
        select(SessionArtifact.identity_id)
        .where(
            SessionArtifact.workspace_id == workspace_id,
            SessionArtifact.deleted_at.is_(None),
            SessionArtifact.identity_id.is_not(None),
            SessionArtifact.finished_at >= cutoff,
        )
        .group_by(SessionArtifact.identity_id)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [row for row in rows if row is not None]


async def identity_belongs_to_workspace(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> bool:
    """Sanity check used by the manual extract endpoint.

    Cross-workspace tokens never reach the per-identity row anyway
    because the repo filters by ``workspace_id``; this guard makes
    the failure mode (no membership in the active workspace) clean
    instead of silently falling through to "0 artifacts examined".
    """
    from app.repositories.workspace import MembershipRepository

    pairs = await MembershipRepository(db).list_with_workspace_for_identity(identity_id)
    return any(ws.id == workspace_id for _mem, ws in pairs)


async def get_identity_or_none(db: AsyncSession, *, identity_id: uuid.UUID) -> Identity | None:
    return await db.get(Identity, identity_id)
