"""M3.4 — per-agent profile aggregation service.

Two-sided modelling: the ``Agent`` side of the equation (the user side
lives under M3.7's ``user_profile`` namespace and is owned by a
sibling subagent — this module deliberately stops at the agent
boundary).

Aggregation pipeline
--------------------

For one agent inside one workspace, the daily sweep (and the manual
``/refresh`` endpoint) computes:

1. **Strengths** — from ``session_artifacts`` rows whose
   ``agent_id`` matches and ``finished_at >= since``. Successful
   artifacts (``judge_score`` >= 0 or NULL with ``final_outcome ==
   "success"``) drive the toolset / skill-category / domain
   buckets. Pure SQL aggregation, no LLM.
2. **Failure modes** — from artifacts with ``judge_score == -1``
   joined to their :class:`JudgeVerdict.process_notes_json`. The
   aux LLM (task ``SKILL_REVIEW``) clusters notes into
   ``hallucination_kinds`` / ``common_errors`` /
   ``error_patterns``. When the workspace evolver breaker is open
   the aux call is skipped and the field stays empty (the row is
   still updated so ``last_aggregated_at`` advances).
3. **Cross-workspace stats** — only computed by the dedicated
   platform-admin path; it scans every workspace this agent's
   ``id`` ever appeared in (an agent typically lives in exactly
   one workspace, but cloned / cross-imported agents technically
   share an id chain — the rollup tolerates either shape).

Audit action keys (verbatim — referenced by tests + UI filters):

* ``agent_profile.updated``
* ``agent_profile.refresh_triggered``
* ``agent_profile.aux_skipped``
* ``agent_profile.cross_workspace_stats_accessed``
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
)
from app.core.errors import CrossWorkspaceStatsForbidden, NotFound
from app.core.security import utcnow_naive
from app.db.models.agent import Agent
from app.db.models.agent_profile import AgentProfile
from app.db.models.identity import Identity, PlatformRole
from app.db.models.judge_verdict import JudgeVerdict
from app.db.models.session_artifact import SessionArtifact
from app.db.models.skills import SkillPack
from app.jobs._breaker import is_breaker_open
from app.repositories.agent_profile import AgentProfileRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_AUX_SKIPPED",
    "AUDIT_CROSS_WORKSPACE_ACCESSED",
    "AUDIT_REFRESH_TRIGGERED",
    "AUDIT_UPDATED",
    "DEFAULT_SINCE_DAYS",
    "EVOLVER_BREAKER_BUCKET",
    "FailureModeUpdate",
    "ProfileUpdateOutcome",
    "aggregate_failure_modes",
    "aggregate_strengths",
    "compute_cross_workspace_stats",
    "get_profile",
    "get_profile_with_cross_workspace_stats",
    "update_profile_for_agent",
]


# ─── Constants ───────────────────────────────────────────────
AUDIT_UPDATED = "agent_profile.updated"
AUDIT_REFRESH_TRIGGERED = "agent_profile.refresh_triggered"
AUDIT_AUX_SKIPPED = "agent_profile.aux_skipped"
AUDIT_CROSS_WORKSPACE_ACCESSED = "agent_profile.cross_workspace_stats_accessed"

# Reuse the evolver breaker bucket: the aux LLM that clusters
# failure modes is the same provider lane as the evolver workflow,
# so a single sick aux model trips both signals together. M3.4
# therefore *reads* the breaker but never *bumps* it — failure
# bumping stays the responsibility of the original consumer
# (evolver / judge / summarize).
EVOLVER_BREAKER_BUCKET = "evolver"

DEFAULT_SINCE_DAYS = 30

# Hard cap on artifacts examined per (agent, workspace) per pass.
# Keeps the SQL footprint bounded regardless of backlog size; the
# stats are stratified, not exhaustive.
_STRENGTH_LIMIT = 500

# Cap on artifacts (and therefore JudgeVerdict process_notes) fed
# to the aux clusterer. 100 is the documented default in the
# brief — enough to identify recurring patterns, small enough to
# fit one aux call.
_FAILURE_MAX_ARTIFACTS = 100

# Cluster threshold: a bucket needs at least this many failing
# artifacts before it becomes a stable "this agent fails this
# way" signal. Single-incident counts are too thin.
_MIN_CLUSTER_SIZE = 2


# ─── Data carriers ──────────────────────────────────────────
@dataclass(slots=True)
class FailureModeUpdate:
    """Internal carrier for the failure-modes aggregate pass."""

    failure_modes: dict[str, Any]
    aux_skipped: bool = False
    aux_skip_reason: str | None = None
    artifacts_examined: int = 0


@dataclass(slots=True)
class ProfileUpdateOutcome:
    """Return shape for :func:`update_profile_for_agent`."""

    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    profile: AgentProfile
    aggregated_run_count: int
    sample_size: int
    aux_skipped: bool = False
    aux_skip_reason: str | None = None
    duration_ms: int = 0
    strengths_buckets: int = 0
    failure_modes_buckets: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    extra: dict[str, Any] = field(default_factory=dict)


class _FailureClusterDraft(BaseModel):
    """Structured aux output the failure-mode clusterer expects."""

    hallucination_kinds: list[dict[str, Any]] = Field(default_factory=list)
    common_errors: list[dict[str, Any]] = Field(default_factory=list)
    error_patterns: list[dict[str, Any]] = Field(default_factory=list)


# ─── Strengths aggregation ──────────────────────────────────
async def aggregate_strengths(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    since: datetime,
    limit: int = _STRENGTH_LIMIT,
) -> dict[str, Any]:
    """Compute the toolsets / skill_categories / domains buckets.

    Successful artifacts are anything where the judge has not voted
    -1 — both unjudged-but-completed runs and runs the judge scored
    >= 0 contribute. Cancelled / errored runs are dropped because
    they don't represent skill effectiveness.
    """
    rows = list(
        await _list_recent_for_agent(
            db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            since=since,
            limit=int(limit),
        )
    )

    successful: list[SessionArtifact] = [
        r
        for r in rows
        if r.final_outcome == "success"
        and (r.judge_score is None or float(r.judge_score) >= 0.0)
    ]

    tool_count: Counter[str] = Counter()
    tool_scores: dict[str, list[float]] = {}
    pack_id_count: Counter[str] = Counter()
    domain_count: Counter[str] = Counter()
    domain_scores: dict[str, list[float]] = {}

    for art in successful:
        score = float(art.judge_score) if art.judge_score is not None else 0.0
        for tool in art.invoked_tools or ():
            if not tool:
                continue
            tool_str = str(tool)
            tool_count[tool_str] += 1
            tool_scores.setdefault(tool_str, []).append(score)
        for pack in art.injected_skill_pack_ids or ():
            if not pack:
                continue
            pack_id_count[str(pack)] += 1

        domain = _extract_domain(art)
        if domain:
            domain_count[domain] += 1
            domain_scores.setdefault(domain, []).append(score)

    skill_category_buckets = await _bucket_skill_categories(
        db, workspace_id=workspace_id, pack_id_counts=pack_id_count
    )

    toolsets = [
        {
            "name": name,
            "use_count": int(count),
            "effectiveness_avg": _safe_mean(tool_scores.get(name) or []),
        }
        for name, count in tool_count.most_common(20)
    ]

    domains = [
        {
            "domain": domain,
            "use_count": int(count),
            "judge_avg": _safe_mean(domain_scores.get(domain) or []),
        }
        for domain, count in domain_count.most_common(10)
    ]

    return {
        "toolsets": toolsets,
        "skill_categories": skill_category_buckets,
        "domains": domains,
        "sample_artifact_count": len(successful),
    }


def _extract_domain(art: SessionArtifact) -> str | None:
    """Best-effort domain extractor.

    The ``SessionArtifact`` row itself doesn't carry a domain field;
    the typical anchor is the workspace's session metadata. The
    artifact's ``error_kind`` is *not* a domain, so we only fall
    back to the dominant ``invoked_tools`` family when nothing
    richer is available — those tool families are good enough
    proxies for "what business domain did this run touch?" for the
    UI's badge list.
    """
    tools = list(art.invoked_tools or ())
    if not tools:
        return None
    head = str(tools[0]).split(".")[0].split("-")[0].split("_")[0].strip().lower()
    return head or None


async def _list_recent_for_agent(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    since: datetime,
    limit: int,
) -> Sequence[SessionArtifact]:
    """Workspace-scoped recent artifacts for one agent, finished_at desc."""
    stmt = (
        select(SessionArtifact)
        .where(
            SessionArtifact.workspace_id == workspace_id,
            SessionArtifact.agent_id == agent_id,
            SessionArtifact.deleted_at.is_(None),
            SessionArtifact.finished_at >= since,
        )
        .order_by(SessionArtifact.finished_at.desc())
        .limit(int(limit))
    )
    return (await db.execute(stmt)).scalars().all()


async def _bucket_skill_categories(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id_counts: Counter[str],
) -> list[dict[str, Any]]:
    """Bucket injected skill packs by the ``manifest_json["tags"]``.

    Packs without tags fall into the ``"general"`` bucket so the
    chart never has unlabelled slivers.
    """
    if not pack_id_counts:
        return []

    pack_uuids: list[uuid.UUID] = []
    for raw in pack_id_counts:
        try:
            pack_uuids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not pack_uuids:
        return []

    stmt = select(SkillPack).where(
        SkillPack.workspace_id == workspace_id,
        SkillPack.id.in_(pack_uuids),
        SkillPack.deleted_at.is_(None),
    )
    rows = list((await db.execute(stmt)).scalars().all())

    by_category: dict[str, int] = {}
    for pack in rows:
        manifest = pack.manifest_json or {}
        raw_tags = manifest.get("tags") if isinstance(manifest, dict) else None
        if isinstance(raw_tags, list) and raw_tags:
            tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()]
        else:
            tags = []
        if not tags:
            tags = ["general"]
        weight = int(pack_id_counts.get(str(pack.id), 0))
        if weight <= 0:
            continue
        for tag in tags:
            by_category[tag] = by_category.get(tag, 0) + weight

    return [
        {"category": cat, "use_count": int(count)}
        for cat, count in sorted(
            by_category.items(), key=lambda kv: (-kv[1], kv[0])
        )[:15]
    ]


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    try:
        return round(float(statistics.fmean(values)), 4)
    except statistics.StatisticsError:
        return None


# ─── Failure modes aggregation ──────────────────────────────
async def aggregate_failure_modes(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    since: datetime,
    max_artifacts: int = _FAILURE_MAX_ARTIFACTS,
) -> FailureModeUpdate:
    """Cluster recent score=-1 artifacts into failure modes.

    Pulls JudgeVerdict.process_notes_json for the matching
    artifacts, asks the aux LLM to cluster, and returns a
    :class:`FailureModeUpdate` carrier so the caller can decide
    whether the breaker-skip path needs an audit row.
    """
    artifacts = await _list_failing_artifacts(
        db,
        workspace_id=workspace_id,
        agent_id=agent_id,
        since=since,
        limit=int(max_artifacts),
    )
    if not artifacts:
        return FailureModeUpdate(
            failure_modes={
                "hallucination_kinds": [],
                "common_errors": [],
                "error_patterns": [],
            },
            artifacts_examined=0,
        )

    artifact_ids = [a.id for a in artifacts]
    verdicts = await _list_verdicts(
        db, workspace_id=workspace_id, artifact_ids=artifact_ids
    )

    # Count common error_kinds straight off the artifacts so the
    # heuristic baseline is non-empty even when no aux model is
    # available.
    err_counter: Counter[str] = Counter()
    for art in artifacts:
        if art.error_kind:
            err_counter[str(art.error_kind)] += 1

    heuristic_common = [
        {"error_kind": k, "count": int(v)}
        for k, v in err_counter.most_common(10)
        if v >= _MIN_CLUSTER_SIZE
    ]

    breaker_open = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=5,
    )
    if breaker_open:
        return FailureModeUpdate(
            failure_modes={
                "hallucination_kinds": [],
                "common_errors": heuristic_common,
                "error_patterns": [],
            },
            aux_skipped=True,
            aux_skip_reason="breaker_open",
            artifacts_examined=len(artifacts),
        )

    aux_config = await get_aux_model(
        db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW
    )
    if aux_config is None:
        return FailureModeUpdate(
            failure_modes={
                "hallucination_kinds": [],
                "common_errors": heuristic_common,
                "error_patterns": [],
            },
            aux_skipped=True,
            aux_skip_reason="no_aux_model",
            artifacts_examined=len(artifacts),
        )

    cluster = await _cluster_with_aux(
        config=aux_config,
        artifacts=artifacts,
        verdicts=verdicts,
    )
    if cluster is None:
        return FailureModeUpdate(
            failure_modes={
                "hallucination_kinds": [],
                "common_errors": heuristic_common,
                "error_patterns": [],
            },
            aux_skipped=True,
            aux_skip_reason="aux_failure",
            artifacts_examined=len(artifacts),
        )

    common_errors = list(cluster.common_errors) if cluster.common_errors else heuristic_common
    return FailureModeUpdate(
        failure_modes={
            "hallucination_kinds": _shape_dict_list(
                cluster.hallucination_kinds, primary_key="kind"
            ),
            "common_errors": _shape_dict_list(common_errors, primary_key="error_kind"),
            "error_patterns": _shape_dict_list(
                cluster.error_patterns, primary_key="pattern_summary"
            ),
        },
        artifacts_examined=len(artifacts),
    )


async def _list_failing_artifacts(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    since: datetime,
    limit: int,
) -> list[SessionArtifact]:
    stmt = (
        select(SessionArtifact)
        .where(
            SessionArtifact.workspace_id == workspace_id,
            SessionArtifact.agent_id == agent_id,
            SessionArtifact.deleted_at.is_(None),
            SessionArtifact.finished_at >= since,
            SessionArtifact.judge_score.is_not(None),
            SessionArtifact.judge_score < 0,
        )
        .order_by(SessionArtifact.finished_at.desc())
        .limit(int(limit))
    )
    return list((await db.execute(stmt)).scalars().all())


async def _list_verdicts(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, JudgeVerdict]:
    if not artifact_ids:
        return {}
    stmt = select(JudgeVerdict).where(
        JudgeVerdict.workspace_id == workspace_id,
        JudgeVerdict.artifact_id.in_(list(artifact_ids)),
        JudgeVerdict.deleted_at.is_(None),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {row.artifact_id: row for row in rows}


def _shape_dict_list(
    items: list[Any], *, primary_key: str
) -> list[dict[str, Any]]:
    """Coerce aux output into a stable list-of-dicts shape.

    The aux model occasionally returns bare strings or partial
    dicts; this helper flattens to ``{<primary_key>: ..., "count":
    ...}`` so the persisted JSONB stays predictable for the UI.
    """
    out: list[dict[str, Any]] = []
    for raw in items[:20]:
        if isinstance(raw, dict):
            entry = dict(raw)
            if primary_key not in entry:
                first_key = next(iter(entry.keys()), None)
                if first_key is not None:
                    entry[primary_key] = str(entry[first_key])
                else:
                    continue
            entry.setdefault("count", 1)
            out.append(entry)
        elif isinstance(raw, str) and raw.strip():
            out.append({primary_key: raw.strip(), "count": 1})
    return out


def _build_cluster_user_prompt(
    artifacts: Sequence[SessionArtifact],
    verdicts: dict[uuid.UUID, JudgeVerdict],
) -> str:
    """Compress N failing artifacts into a single aux call body."""
    parts: list[str] = []
    parts.append(f"Failing artifacts: {len(artifacts)}")
    for idx, art in enumerate(artifacts[:30]):
        verdict = verdicts.get(art.id)
        notes = []
        if verdict is not None:
            raw_notes = verdict.process_notes_json or []
            for note in raw_notes[:3]:
                if isinstance(note, str) and note.strip():
                    notes.append(note.strip()[:240])
        line = (
            f"#{idx} error_kind={art.error_kind or 'none'} "
            f"tools={','.join(art.invoked_tools or [])[:120]} "
            f"score={art.judge_score} "
        )
        if notes:
            line += "notes=" + " | ".join(notes)
        parts.append(line[:600])
    return "\n".join(parts)[:8000]


_CLUSTER_SYSTEM_PROMPT = (
    "You are an internal failure-mode clusterer. Given a list of "
    "agent runs that the run-quality judge scored -1, group them "
    "into three buckets:\n"
    " * hallucination_kinds — invented tool args, fabricated "
    "results, fake citations.\n"
    " * common_errors — concrete external failures (rate_limit, "
    "auth, validation, parse).\n"
    " * error_patterns — recurring behavioural mistakes that span "
    "multiple kinds (e.g. 'forgets to handle empty results').\n"
    "Return JSON of the form {\"hallucination_kinds\":[{\"kind\":"
    "...,\"count\":N}], \"common_errors\":[{\"error_kind\":...,"
    "\"count\":N}], \"error_patterns\":[{\"pattern_summary\":...,"
    "\"frequency\":N}]}. Only include buckets with at least 2 "
    "matching runs. Be specific; avoid generic English."
)


async def _cluster_with_aux(
    *,
    config: AuxiliaryConfig,
    artifacts: Sequence[SessionArtifact],
    verdicts: dict[uuid.UUID, JudgeVerdict],
) -> _FailureClusterDraft | None:
    user_prompt = _build_cluster_user_prompt(artifacts, verdicts)
    response = await call_aux_chat(
        config=config,
        system=_CLUSTER_SYSTEM_PROMPT,
        user=user_prompt,
        response_format=_FailureClusterDraft,
        timeout_s=30.0,
    )
    if isinstance(response, _FailureClusterDraft):
        return response
    return None


# ─── Cross-workspace stats ──────────────────────────────────
async def compute_cross_workspace_stats(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Cross-tenant rollup for the platform-admin endpoint.

    Scans every artifact whose ``agent_id`` matches regardless of
    workspace. Counts total runs, computes the median judge score,
    and emits the top failure ``error_kind`` bucket. The result is
    intentionally compact — the platform admin only needs
    "is this agent healthy across all installs?".
    """
    base_filter = [
        SessionArtifact.agent_id == agent_id,
        SessionArtifact.deleted_at.is_(None),
    ]
    if since is not None:
        base_filter.append(SessionArtifact.finished_at >= since)

    count_stmt = select(func.count()).select_from(SessionArtifact).where(*base_filter)
    total_runs = int((await db.execute(count_stmt)).scalar() or 0)

    score_stmt = (
        select(SessionArtifact.judge_score)
        .where(
            *base_filter,
            SessionArtifact.judge_score.is_not(None),
        )
    )
    raw_scores = [
        float(s) for s in (await db.execute(score_stmt)).scalars().all()
    ]

    median_score: float | None = None
    if raw_scores:
        try:
            median_score = round(float(statistics.median(raw_scores)), 4)
        except statistics.StatisticsError:
            median_score = None

    err_stmt = (
        select(SessionArtifact.error_kind, func.count())
        .where(*base_filter, SessionArtifact.error_kind.is_not(None))
        .group_by(SessionArtifact.error_kind)
        .order_by(func.count().desc())
        .limit(10)
    )
    err_rows = (await db.execute(err_stmt)).all()
    top_failure_kinds = [
        {"error_kind": str(row[0]), "count": int(row[1])}
        for row in err_rows
        if row[0] is not None
    ]

    workspace_count_stmt = (
        select(func.count(func.distinct(SessionArtifact.workspace_id)))
        .where(*base_filter)
    )
    workspace_count = int(
        (await db.execute(workspace_count_stmt)).scalar() or 0
    )

    return {
        "total_runs_across_tenants": total_runs,
        "median_judge_score": median_score,
        "top_failure_kinds": top_failure_kinds,
        "workspace_count": workspace_count,
        "judged_run_count": len(raw_scores),
    }


# ─── Public read paths ──────────────────────────────────────
async def get_profile(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentProfile | None:
    """Workspace-scoped read; cross-workspace stats are *not* exposed.

    Returns ``None`` when no profile row has been computed yet — the
    API layer surfaces that as a 404 with a stable code.
    """
    return await AgentProfileRepository(db).get_by_agent(
        workspace_id=workspace_id, agent_id=agent_id
    )


async def get_profile_with_cross_workspace_stats(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    actor: Identity,
) -> AgentProfile:
    """Platform-admin read — also writes the access audit.

    Raises :class:`CrossWorkspaceStatsForbidden` when the caller is
    not a platform admin. The workspace-scoped read endpoint must
    never reach this function.
    """
    if actor.platform_role != PlatformRole.PLATFORM_ADMIN:
        raise CrossWorkspaceStatsForbidden(
            "platform_admin_required",
            code="agent_profile.cross_workspace_forbidden",
        )

    repo = AgentProfileRepository(db)
    profile = await repo.get_by_agent_any_workspace(agent_id=agent_id)
    if profile is None:
        raise NotFound(
            "agent_profile_missing",
            code="agent_profile.not_found",
        )

    fresh_stats = await compute_cross_workspace_stats(db, agent_id=agent_id)
    profile.cross_workspace_stats_json = fresh_stats
    await db.flush([profile])

    await audit_svc.record(
        db,
        action=AUDIT_CROSS_WORKSPACE_ACCESSED,
        actor_identity_id=actor.id,
        workspace_id=profile.workspace_id,
        resource_type="agent_profile",
        resource_id=profile.id,
        summary="platform admin read agent_profile cross_workspace stats",
        metadata={
            "agent_id": str(agent_id),
            "workspace_count": int(
                fresh_stats.get("workspace_count", 0) or 0
            ),
            "total_runs": int(
                fresh_stats.get("total_runs_across_tenants", 0) or 0
            ),
        },
    )
    return profile


# ─── Update entry point ─────────────────────────────────────
async def update_profile_for_agent(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    since_days: int = DEFAULT_SINCE_DAYS,
    invocation_kind: str = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
) -> ProfileUpdateOutcome:
    """Aggregate strengths + failure modes and persist the row.

    Idempotent: re-running for the same agent overwrites the
    ``strengths_json`` / ``failure_modes_json`` slices but never
    touches ``cross_workspace_stats_json`` (that lives behind the
    platform-admin gate).

    Audits one ``agent_profile.updated`` row per successful run plus
    a separate ``agent_profile.aux_skipped`` row when the failure-
    mode pass had to bail out (breaker / no aux / aux failure).
    """
    started_at = utcnow_naive()
    started_perf = _perf_counter()

    agent = await db.get(Agent, agent_id)
    if agent is None or agent.deleted_at is not None:
        raise NotFound("agent_missing", code="agent.not_found")
    if agent.workspace_id != workspace_id:
        # Defensive — service callers must pass the matching pair.
        raise NotFound("agent_workspace_mismatch", code="agent.not_found")

    since = started_at - timedelta(days=int(since_days))

    strengths = await aggregate_strengths(
        db,
        workspace_id=workspace_id,
        agent_id=agent_id,
        since=since,
    )
    failure = await aggregate_failure_modes(
        db,
        workspace_id=workspace_id,
        agent_id=agent_id,
        since=since,
    )

    repo = AgentProfileRepository(db)
    profile = await repo.get_by_agent(
        workspace_id=workspace_id, agent_id=agent_id
    )
    aggregated_run_count = int(strengths.get("sample_artifact_count", 0)) + int(
        failure.artifacts_examined
    )
    sample_size = int(strengths.get("sample_artifact_count", 0))

    if profile is None:
        profile = await repo.create(
            workspace_id=workspace_id,
            agent_id=agent_id,
            strengths_json=strengths,
            failure_modes_json=failure.failure_modes,
            cross_workspace_stats_json={},
            last_aggregated_at=started_at,
            aggregated_run_count=aggregated_run_count,
            sample_size=sample_size,
        )
    else:
        profile.strengths_json = strengths
        profile.failure_modes_json = failure.failure_modes
        profile.last_aggregated_at = started_at
        profile.aggregated_run_count = aggregated_run_count
        profile.sample_size = sample_size
        if profile.deleted_at is not None:
            profile.deleted_at = None
        await db.flush([profile])

    duration_ms = int((_perf_counter() - started_perf) * 1000)
    finished_at = utcnow_naive()

    audit_meta: dict[str, Any] = {
        "agent_id": str(agent_id),
        "invocation_kind": invocation_kind,
        "since_days": int(since_days),
        "duration_ms": duration_ms,
        "aggregated_run_count": aggregated_run_count,
        "sample_size": sample_size,
        "strengths_buckets": _bucket_count(strengths),
        "failure_modes_buckets": _failure_bucket_count(failure.failure_modes),
        "aux_skipped": failure.aux_skipped,
        "aux_skip_reason": failure.aux_skip_reason,
    }
    await audit_svc.record(
        db,
        action=AUDIT_UPDATED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="agent_profile",
        resource_id=profile.id,
        summary=(
            f"agent_profile updated ({invocation_kind}): "
            f"{aggregated_run_count} runs aggregated"
        ),
        metadata=audit_meta,
    )

    if failure.aux_skipped:
        await audit_svc.record(
            db,
            action=AUDIT_AUX_SKIPPED,
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="agent_profile",
            resource_id=profile.id,
            summary=(
                f"agent_profile aux failure-mode pass skipped: "
                f"{failure.aux_skip_reason or 'unknown'}"
            ),
            metadata={
                "agent_id": str(agent_id),
                "skip_reason": failure.aux_skip_reason,
                "artifacts_examined": failure.artifacts_examined,
                "invocation_kind": invocation_kind,
                "breaker_bucket": EVOLVER_BREAKER_BUCKET,
            },
        )

    return ProfileUpdateOutcome(
        workspace_id=workspace_id,
        agent_id=agent_id,
        profile=profile,
        aggregated_run_count=aggregated_run_count,
        sample_size=sample_size,
        aux_skipped=failure.aux_skipped,
        aux_skip_reason=failure.aux_skip_reason,
        duration_ms=duration_ms,
        strengths_buckets=_bucket_count(strengths),
        failure_modes_buckets=_failure_bucket_count(failure.failure_modes),
        started_at=started_at,
        finished_at=finished_at,
    )


def _bucket_count(strengths: dict[str, Any]) -> int:
    return (
        len(strengths.get("toolsets") or [])
        + len(strengths.get("skill_categories") or [])
        + len(strengths.get("domains") or [])
    )


def _failure_bucket_count(failure_modes: dict[str, Any]) -> int:
    return (
        len(failure_modes.get("hallucination_kinds") or [])
        + len(failure_modes.get("common_errors") or [])
        + len(failure_modes.get("error_patterns") or [])
    )


def _perf_counter() -> float:
    """Indirection for the time source so tests can monkeypatch.

    Kept inside this module rather than importing ``time.perf_counter``
    directly so an integration test that wants deterministic durations
    can swap the symbol without touching the rest of stdlib.
    """
    import time

    return time.perf_counter()
