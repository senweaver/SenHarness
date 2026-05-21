"""Daily evolver workflow engine (M2.3).

Five-stage workflow that drains recent low-scoring artifacts in a
workspace, summarises them with the aux LLM, clusters them by failure
shape, and files SkillPack proposals via the M2.7 propose verbs.

Two engine modes share the drain + summarize stages:

* ``engine="workflow"`` — deterministic pipeline. The aggregate stage
  buckets artifacts by ``error_kind`` / dominant invoked tool; the
  evolve stage asks the aux LLM (with structured output) to draft a
  ``content_md`` for each cluster, then calls
  :func:`app.agents.tools.skill_propose.run_propose_skill_create`
  directly (no agent loop, no model creativity past the structured
  draft).
* ``engine="agent"`` — delegates to
  :func:`app.agents.builtin.evolver_agent.invoke_evolver_subagent`
  with the drain summary plumbed in as ``seed_message`` so the
  evolver agent gets a head-start instead of having to call
  ``list_session_artifacts`` itself.

Both modes:

* Skip when the workspace has fewer than ``min_artifacts_per_evolution``
  failing artifacts in the lookback window (overridable per call via
  ``bypass_min_artifacts=True`` for the manual trigger endpoint).
* Skip when the evolver is disabled or the breaker is open.
* Reset the shared ``evolver:fail:<workspace_id>`` breaker when at
  least one proposal was filed successfully (the propose pipeline is
  end-to-end healthy).
* Audit one ``evolver.workflow_completed`` row with the structured
  metadata that the M2.3 admin UI / M4.6 dashboard reads.

Audit action keys (verbatim — referenced by tests + UI filters):

* ``evolver.workflow_completed``
* ``evolver.workflow_skipped``
* ``evolver.workflow_failed``
* ``evolver.manually_triggered``
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
)
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.skill_propose import (
    EVOLVER_BREAKER_BUCKET,
    ProposeSkillCreateArgs,
    run_propose_skill_create,
)
from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.session import get_session_factory
from app.jobs._breaker import is_breaker_open, reset_failure
from app.repositories.session_artifact import SessionArtifactRepository
from app.repositories.skills import SkillPackRepository
from app.schemas.platform_settings import EvolverSettings
from app.services import audit as audit_svc
from app.services.evolver_config import get_workspace_evolver_config

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_FAILED",
    "AUDIT_MANUALLY_TRIGGERED",
    "AUDIT_SKIPPED",
    "AUDIT_WORKFLOW_COMPLETED",
    "DEFAULT_LOOKBACK_DAYS",
    "DrainSummary",
    "WorkflowExecutionResult",
    "build_drain_summary",
    "evolve_workspace_skills",
    "evolve_workspace_skills_agent",
    "evolve_workspace_skills_workflow",
    "summarize_drain_with_aux",
]


# ─── Constants ───────────────────────────────────────────────
AUDIT_WORKFLOW_COMPLETED = "evolver.workflow_completed"
AUDIT_SKIPPED = "evolver.workflow_skipped"
AUDIT_FAILED = "evolver.workflow_failed"
AUDIT_MANUALLY_TRIGGERED = "evolver.manually_triggered"

# How far back to scan artifacts when computing the drain. Workspace
# overrides ride on ``home_config_json["evolver"]["drain_lookback_days"]``
# but the schema-level field doesn't exist yet; until M4.6 lifts it
# into EvolverSettings the value here is the only knob.
DEFAULT_LOOKBACK_DAYS = 7

# Hard cap on artifacts examined per workspace per run. Keeps the
# aux-LLM call costed at O(1) regardless of backlog size — the
# summary is stratified, not exhaustive.
_DRAIN_LIMIT = 200

# Cluster threshold: a bucket needs at least this many failing
# artifacts to be worth filing a proposal. Two is the floor; one
# isolated failure is too thin a signal to draft a skill from.
_MIN_CLUSTER_SIZE = 2

# Aux-LLM cap on the structural summary handed to the agent / model.
# 600 chars matches the "seed_message" budget the brief calls out.
_SUMMARY_MAX_CHARS = 600

# Slugify guard for proposed skill packs. Short, lowercase, hyphen-
# separated; the propose verb's own pattern is `^[a-z0-9-]+$`.
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


# ─── Result + summary structs ────────────────────────────────
@dataclass(slots=True)
class WorkflowExecutionResult:
    """Outcome envelope returned by every entry point.

    ``proposals_created`` is the count of Approval rows the run filed
    via the propose verbs (the propose verb itself is the source of
    truth — we tally responses, not heuristics). ``error`` is non-None
    only on the unhandled-exception path; deliberate skips populate
    ``skipped=True`` + ``skip_reason`` instead.
    """

    workspace_id: uuid.UUID
    engine: Literal["workflow", "agent"]
    artifacts_drained: int
    artifacts_summarized: int
    proposals_created: int
    skipped: bool
    skip_reason: str | None
    duration_ms: int
    error: str | None = None
    invocation_kind: Literal["scheduled", "manual"] = "scheduled"
    aux_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["workspace_id"] = str(self.workspace_id)
        return d


@dataclass(slots=True)
class DrainSummary:
    """Aggregated structural snapshot across N session_artifacts.

    Carries everything the aggregate + evolve stages need without
    leaking ``user_text`` or full ``turns_json`` blobs out of the
    artifact row. ``sample_artifact_ids`` is the lineage hook the
    propose verbs forward as ``supporting_run_ids``.
    """

    artifact_count: int
    score_distribution: dict[int, int] = field(default_factory=dict)
    common_error_kinds: list[tuple[str, int]] = field(default_factory=list)
    common_invoked_tools: list[tuple[str, int]] = field(default_factory=list)
    sample_artifact_ids: list[uuid.UUID] = field(default_factory=list)
    sample_run_ids: list[uuid.UUID] = field(default_factory=list)

    def is_empty(self) -> bool:
        return self.artifact_count == 0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "artifact_count": int(self.artifact_count),
            "score_distribution": {str(k): int(v) for k, v in self.score_distribution.items()},
            "common_error_kinds": [list(t) for t in self.common_error_kinds],
            "common_invoked_tools": [list(t) for t in self.common_invoked_tools],
            "sample_artifact_ids": [str(x) for x in self.sample_artifact_ids],
        }


# Structured output used by the workflow's evolve stage to draft a
# new skill pack body from a failure cluster. The aux LLM is asked
# for exactly this shape; see ``_draft_skill_for_cluster``.
class _SkillDraft(BaseModel):
    slug: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=10, max_length=400)
    content_md: str = Field(min_length=80, max_length=8000)


# ─── Drain stage ─────────────────────────────────────────────
async def build_drain_summary(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    since: datetime,
    judge_score_max: float = 0.0,
    limit: int = _DRAIN_LIMIT,
) -> DrainSummary:
    """Aggregate failing artifacts into one structural snapshot.

    Scans recent artifacts (since ``since``) for the workspace and
    keeps the ones with ``judge_score < judge_score_max`` (default
    ``0.0`` — i.e. negative judges only; the M0.3 judge writes
    ``-1`` for failures and ``0`` for partials). Counts error_kinds
    and invoked tools across the kept set so both engines have the
    same view of "what's going wrong".
    """
    repo = SessionArtifactRepository(db)
    rows = list(
        await repo.list_recent_for_workspace(
            workspace_id=workspace_id, since=since, limit=int(limit)
        )
    )

    failing: list[SessionArtifact] = [
        r
        for r in rows
        if r.judge_score is not None and float(r.judge_score) < float(judge_score_max)
    ]

    score_dist: dict[int, int] = {}
    err_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    sample_artifact_ids: list[uuid.UUID] = []
    sample_run_ids: list[uuid.UUID] = []

    for art in failing:
        bucket = int(round(float(art.judge_score)))
        score_dist[bucket] = score_dist.get(bucket, 0) + 1
        if art.error_kind:
            err_counter[str(art.error_kind)] += 1
        for tool in art.invoked_tools or ():
            if tool:
                tool_counter[str(tool)] += 1
        if len(sample_artifact_ids) < 10:
            sample_artifact_ids.append(art.id)
            sample_run_ids.append(art.run_id)

    return DrainSummary(
        artifact_count=len(failing),
        score_distribution=score_dist,
        common_error_kinds=err_counter.most_common(10),
        common_invoked_tools=tool_counter.most_common(10),
        sample_artifact_ids=sample_artifact_ids,
        sample_run_ids=sample_run_ids,
    )


# ─── Summarize stage ─────────────────────────────────────────
def _render_summary_seed(summary: DrainSummary) -> str:
    """Compose a compact deterministic seed text from a DrainSummary.

    Used as the fallback when no aux model is configured (so the
    workflow + agent modes still get meaningful seed text) and as
    the user prompt fed to the aux summariser when one is configured.
    """
    if summary.is_empty():
        return "No failing artifacts in the lookback window."

    err_part = (
        ", ".join(f"{k}({v})" for k, v in summary.common_error_kinds[:5])
        or "(no error_kind tagged)"
    )
    tool_part = (
        ", ".join(f"{k}({v})" for k, v in summary.common_invoked_tools[:5])
        or "(no tool calls recorded)"
    )
    score_part = ", ".join(
        f"{k}={v}" for k, v in sorted(summary.score_distribution.items())
    )

    return (
        f"{summary.artifact_count} failing artifact(s) in the lookback window. "
        f"score_distribution: {score_part}. "
        f"top error_kind: {err_part}. "
        f"top invoked_tools: {tool_part}."
    )


async def summarize_drain_with_aux(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    summary: DrainSummary,
    aux_config: AuxiliaryConfig | None = None,
) -> str:
    """Use the aux LLM to compress a DrainSummary into a 600-char seed.

    On any aux-LLM failure (no config, timeout, parse error) returns
    the deterministic :func:`_render_summary_seed` as a graceful
    fallback so the engines downstream still get a usable seed text.
    """
    seed = _render_summary_seed(summary)
    if summary.is_empty():
        return seed

    config = aux_config
    if config is None:
        config = await get_aux_model(
            db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW
        )
    if config is None:
        return seed[:_SUMMARY_MAX_CHARS]

    system = (
        "You are an internal skill-curator summariser. Read the structured "
        f"failure summary and write a single paragraph of at most {_SUMMARY_MAX_CHARS} "
        "characters that an evolver agent will use as the brief for filing "
        "skill-pack proposals. Be concrete: name the dominant error_kind, the "
        "tools involved, and one or two specific patterns worth fixing. Do "
        "not include lists or headings; pure prose."
    )

    response = await call_aux_chat(
        config=config, system=system, user=seed, response_format=None, timeout_s=20.0
    )
    if isinstance(response, str) and response.strip():
        return response.strip()[:_SUMMARY_MAX_CHARS]
    return seed[:_SUMMARY_MAX_CHARS]


# ─── Aggregate stage ────────────────────────────────────────
@dataclass(slots=True)
class _Cluster:
    """Internal — one failure bucket the evolve stage tries to address."""

    label: str
    error_kind: str | None
    invoked_tools: list[str]
    artifact_count: int
    sample_run_ids: list[uuid.UUID]


def _aggregate_clusters(
    summary: DrainSummary, *, min_size: int = _MIN_CLUSTER_SIZE
) -> list[_Cluster]:
    """Bucket the summary by error_kind first, then by dominant tool.

    Returns at most one cluster per distinct error_kind plus one
    catch-all bucket per dominant tool. Buckets with fewer than
    ``min_size`` artifacts are dropped — they're too thin to draft
    a useful skill from.
    """
    clusters: list[_Cluster] = []
    used_run_ids: set[uuid.UUID] = set()

    fallback_runs = list(summary.sample_run_ids)

    for error_kind, count in summary.common_error_kinds:
        if count < min_size:
            continue
        sample = [r for r in fallback_runs if r not in used_run_ids][:5]
        used_run_ids.update(sample)
        clusters.append(
            _Cluster(
                label=f"error:{error_kind}",
                error_kind=error_kind,
                invoked_tools=[t for t, _ in summary.common_invoked_tools[:3]],
                artifact_count=int(count),
                sample_run_ids=sample,
            )
        )

    if not clusters:
        for tool, count in summary.common_invoked_tools:
            if count < min_size:
                continue
            sample = [r for r in fallback_runs if r not in used_run_ids][:5]
            used_run_ids.update(sample)
            clusters.append(
                _Cluster(
                    label=f"tool:{tool}",
                    error_kind=None,
                    invoked_tools=[tool],
                    artifact_count=int(count),
                    sample_run_ids=sample,
                )
            )
            if len(clusters) >= 3:
                break

    return clusters


# ─── Evolve stage ───────────────────────────────────────────
def _slugify_for_skill(label: str) -> str:
    """Produce a propose-verb-safe slug from a cluster label."""
    cleaned = _SLUG_PATTERN.sub("-", label.lower()).strip("-")
    if not cleaned:
        cleaned = "evolver-draft"
    return cleaned[:60]


def _humanise_for_name(label: str) -> str:
    return label.replace("-", " ").replace("_", " ").replace(":", " — ").title()


def _fallback_draft(cluster: _Cluster, summary_text: str) -> _SkillDraft:
    """Deterministic draft used when no aux model is available.

    Keeps the workflow path useful even on a cold workspace with no
    aux model: the resulting proposal is generic but rich enough to
    seed a real human-driven skill once the admin reviews it.
    """
    base_slug = _slugify_for_skill(cluster.label)
    short_label = _humanise_for_name(cluster.label)
    tools_line = (
        ", ".join(cluster.invoked_tools) if cluster.invoked_tools else "(none recorded)"
    )
    error_line = cluster.error_kind or "(no error_kind tagged)"
    body = (
        f"# {short_label}\n\n"
        f"Drafted by the evolver workflow from {cluster.artifact_count} failing "
        f"runs in the recent lookback window.\n\n"
        f"## Observed failure shape\n\n"
        f"- Dominant error_kind: `{error_line}`\n"
        f"- Tools involved: {tools_line}\n\n"
        f"## Drain summary\n\n{summary_text}\n\n"
        "## Suggested handling\n\n"
        "Replace this section with a concrete checklist for the agent: "
        "preconditions to verify, fall-back tools, and rejection criteria. "
        "This draft is a starting point — review and rewrite before "
        "approving.\n"
    )
    return _SkillDraft(
        slug=base_slug,
        name=short_label,
        description=(
            f"Evolver-drafted skill addressing recent failures "
            f"(error_kind={error_line}, samples={cluster.artifact_count})."
        )[:400],
        content_md=body,
    )


async def _draft_skill_for_cluster(
    *,
    cluster: _Cluster,
    summary_text: str,
    aux_config: AuxiliaryConfig | None,
) -> _SkillDraft:
    """Ask the aux LLM for a structured skill draft, fall back on failure."""
    if aux_config is None:
        return _fallback_draft(cluster, summary_text)

    system = (
        "You are drafting a SkillPack body the workspace admin will review. "
        "Output the structured fields only. Slug must match `^[a-z0-9-]+$`, "
        "be a short kebab-case identifier (3-64 chars). content_md must be "
        "a Markdown body of 80-8000 characters. Be specific: name the failure "
        "shape, list preconditions, name the tools involved, and propose a "
        "concrete handling checklist. Do not invent unrelated content."
    )
    user = (
        f"Cluster label: {cluster.label}\n"
        f"Dominant error_kind: {cluster.error_kind or '(none)'}\n"
        f"Tools involved: {', '.join(cluster.invoked_tools) or '(none)'}\n"
        f"Failing artifacts in cluster: {cluster.artifact_count}\n\n"
        f"Drain summary:\n{summary_text}"
    )

    response = await call_aux_chat(
        config=aux_config,
        system=system,
        user=user,
        response_format=_SkillDraft,
        timeout_s=30.0,
    )
    if isinstance(response, _SkillDraft):
        return response
    return _fallback_draft(cluster, summary_text)


async def _ensure_unique_slug(
    db: AsyncSession, *, workspace_id: uuid.UUID, base_slug: str
) -> str:
    """Return a slug that doesn't collide with an existing pack.

    The propose verb itself rejects slug conflicts hard, so the
    workflow probes here to keep the proposal landing rate high. We
    add a short suffix on collision; truly tombstoned slugs still
    fail downstream (the verb's tombstone gate is the canonical
    enforcement point, this is a best-effort smoothing).
    """
    repo = SkillPackRepository(db)
    candidate = base_slug
    for suffix in ("", "-v2", "-v3", f"-{uuid.uuid4().hex[:6]}"):
        slug = (base_slug + suffix)[:60].strip("-") or "evolver-draft"
        existing = await repo.get_by_slug(workspace_id=workspace_id, slug=slug)
        if existing is None:
            return slug
        candidate = slug
    return candidate


async def _file_proposal_for_cluster(
    *,
    workspace_id: uuid.UUID,
    cluster: _Cluster,
    draft: _SkillDraft,
    actor_identity_id: uuid.UUID | None,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """Fix the slug + dispatch the propose_skill_create runner.

    Sets a fresh ToolRunContext so the propose verb's audit + breaker
    logic see the workflow's run_id (one ``run_id`` per workflow
    execution; the verb writes one Approval row per cluster scoped
    to that id, so the M2.4 verifier can join).
    """
    factory = get_session_factory()
    async with factory() as db:
        unique_slug = await _ensure_unique_slug(
            db, workspace_id=workspace_id, base_slug=draft.slug
        )

    args = ProposeSkillCreateArgs(
        rationale=(
            f"Workflow draft addressing cluster '{cluster.label}': "
            f"{cluster.artifact_count} failing artifact(s)."
        )[:1000],
        slug=unique_slug,
        name=draft.name,
        description=draft.description,
        content_md=draft.content_md,
        files=None,
        supporting_run_ids=[str(r) for r in cluster.sample_run_ids[:20]],
    )

    sentinel_session_id = uuid.uuid5(
        uuid.NAMESPACE_DNS, f"evolver:workflow:session:{run_id}"
    )
    sentinel_agent_id = uuid.uuid5(
        uuid.NAMESPACE_DNS, f"evolver:workflow:agent:{workspace_id}"
    )
    ctx = ToolRunContext(
        run_id=run_id,
        workspace_id=workspace_id,
        session_id=sentinel_session_id,
        identity_id=actor_identity_id or sentinel_agent_id,
        agent_id=sentinel_agent_id,
        scratch_base=Path("."),
        policy={
            "agent_kind": "evolver",
            "workspace_id": str(workspace_id),
            "invocation_kind": "workflow",
        },
    )

    set_context(ctx)
    try:
        return await run_propose_skill_create(args)
    finally:
        set_context(None)


# ─── Audit + breaker helpers ────────────────────────────────
async def _record_audit(
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    action: str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="workspace",
                resource_id=workspace_id,
                summary=summary,
                metadata=metadata,
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("evolver workflow audit failed action=%s", action)


async def _maybe_reset_breaker(
    *, workspace_id: uuid.UUID, proposals_created: int
) -> None:
    """Reset the shared evolver breaker on a healthy workflow run."""
    if proposals_created < 1:
        return
    try:
        await reset_failure(
            bucket=EVOLVER_BREAKER_BUCKET, workspace_id=str(workspace_id)
        )
    except Exception:  # pragma: no cover - breaker reset is fail-open
        log.exception("evolver breaker reset failed for workspace=%s", workspace_id)


# ─── Pre-flight gates shared across both engines ─────────────
async def _preflight(
    *,
    workspace_id: uuid.UUID,
    invocation_kind: str,
    actor_identity_id: uuid.UUID | None,
    bypass_min_artifacts: bool,
) -> tuple[EvolverSettings, DrainSummary, str | None]:
    """Resolve config + drain + decide whether to skip.

    Returns ``(config, summary, skip_reason | None)``. Caller short-
    circuits when ``skip_reason`` is not None and writes the matching
    audit row.
    """
    factory = get_session_factory()
    async with factory() as db:
        config = await get_workspace_evolver_config(
            db, workspace_id=workspace_id
        )

    if not config.enabled:
        return config, DrainSummary(artifact_count=0), "evolver_disabled"

    breaker_open = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=int(config.evolver_breaker_strikes),
    )
    if breaker_open:
        return config, DrainSummary(artifact_count=0), "breaker_open"

    since = utcnow_naive() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    async with factory() as db:
        summary = await build_drain_summary(
            db, workspace_id=workspace_id, since=since
        )

    min_required = max(1, int(config.min_artifacts_per_evolution))
    if not bypass_min_artifacts and summary.artifact_count < min_required:
        return config, summary, "insufficient_artifacts"

    _ = invocation_kind, actor_identity_id  # kept for signature symmetry
    return config, summary, None


# ─── Workflow engine implementation ─────────────────────────
async def evolve_workspace_skills_workflow(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    invocation_kind: Literal["scheduled", "manual"] = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
    bypass_min_artifacts: bool = False,
) -> WorkflowExecutionResult:
    """Five-stage workflow pipeline (no LLM creativity past the draft step)."""
    started = time.perf_counter()
    run_id = uuid.uuid4()
    _ = db  # entry-point kept for API symmetry; the implementation owns its own sessions

    config, summary, skip_reason = await _preflight(
        workspace_id=workspace_id,
        invocation_kind=invocation_kind,
        actor_identity_id=actor_identity_id,
        bypass_min_artifacts=bypass_min_artifacts,
    )
    if skip_reason is not None:
        return await _finalise_skip(
            workspace_id=workspace_id,
            engine="workflow",
            invocation_kind=invocation_kind,
            actor_identity_id=actor_identity_id,
            summary=summary,
            skip_reason=skip_reason,
            started=started,
            config=config,
        )

    # ── Stage: summarize ────────────────────────────────
    factory = get_session_factory()
    async with factory() as fresh_db:
        aux_config = await get_aux_model(
            fresh_db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW
        )
        summary_text = await summarize_drain_with_aux(
            fresh_db,
            workspace_id=workspace_id,
            summary=summary,
            aux_config=aux_config,
        )
    aux_model_name = aux_config.model if aux_config is not None else None

    # ── Stage: aggregate ───────────────────────────────
    clusters = _aggregate_clusters(summary)

    # ── Stage: evolve ──────────────────────────────────
    proposals_created = 0
    try:
        for cluster in clusters:
            draft = await _draft_skill_for_cluster(
                cluster=cluster, summary_text=summary_text, aux_config=aux_config
            )
            outcome = await _file_proposal_for_cluster(
                workspace_id=workspace_id,
                cluster=cluster,
                draft=draft,
                actor_identity_id=actor_identity_id,
                run_id=run_id,
            )
            if isinstance(outcome, dict) and outcome.get("status") == "proposed":
                proposals_created += 1
            else:
                log.info(
                    "evolver workflow cluster=%s rejected payload=%s",
                    cluster.label,
                    outcome,
                )
    except Exception as exc:  # noqa: BLE001 - workflow must not raise to ARQ
        duration_ms = int((time.perf_counter() - started) * 1000)
        await _record_audit(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=AUDIT_FAILED,
            summary=f"evolver workflow run failed: {type(exc).__name__}",
            metadata={
                "engine": "workflow",
                "invocation_kind": invocation_kind,
                "run_id": str(run_id),
                "duration_ms": duration_ms,
                "drain": summary.to_metadata(),
                "stage": "evolve",
                "error": f"{type(exc).__name__}: {exc}",
                "aux_model": aux_model_name,
            },
        )
        return WorkflowExecutionResult(
            workspace_id=workspace_id,
            engine="workflow",
            artifacts_drained=summary.artifact_count,
            artifacts_summarized=summary.artifact_count,
            proposals_created=proposals_created,
            skipped=False,
            skip_reason=None,
            duration_ms=duration_ms,
            error=f"{type(exc).__name__}: {exc}",
            invocation_kind=invocation_kind,
            aux_model=aux_model_name,
        )

    # ── Stage: publish (audit + breaker reset) ──────────
    await _maybe_reset_breaker(
        workspace_id=workspace_id, proposals_created=proposals_created
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    await _record_audit(
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
        action=AUDIT_WORKFLOW_COMPLETED,
        summary=(
            f"evolver workflow ({invocation_kind}) completed: "
            f"{proposals_created} proposal(s) from {summary.artifact_count} artifact(s)"
        ),
        metadata={
            "engine": "workflow",
            "invocation_kind": invocation_kind,
            "run_id": str(run_id),
            "duration_ms": duration_ms,
            "stages_succeeded": [
                "drain",
                "summarize",
                "aggregate",
                "evolve",
                "publish",
            ],
            "artifacts_drained": int(summary.artifact_count),
            "artifacts_summarized": int(summary.artifact_count),
            "clusters": [
                {
                    "label": c.label,
                    "error_kind": c.error_kind,
                    "tools": c.invoked_tools,
                    "artifact_count": c.artifact_count,
                }
                for c in clusters
            ],
            "proposals_created": int(proposals_created),
            "drain": summary.to_metadata(),
            "aux_model": aux_model_name,
        },
    )

    return WorkflowExecutionResult(
        workspace_id=workspace_id,
        engine="workflow",
        artifacts_drained=summary.artifact_count,
        artifacts_summarized=summary.artifact_count,
        proposals_created=proposals_created,
        skipped=False,
        skip_reason=None,
        duration_ms=duration_ms,
        error=None,
        invocation_kind=invocation_kind,
        aux_model=aux_model_name,
    )


# ─── Agent engine implementation ────────────────────────────
async def evolve_workspace_skills_agent(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    invocation_kind: Literal["scheduled", "manual"] = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
    bypass_min_artifacts: bool = False,
) -> WorkflowExecutionResult:
    """Build the drain seed text then delegate to the evolver subagent.

    The subagent's existing ``invoke_evolver_subagent`` entry point
    accepts the workflow's seed via the ``triggering_run_ids`` hint —
    the seed text isn't a first-class field there yet (M2.2 lands the
    ``seed_message`` knob in a follow-up), so we wedge the structured
    summary into the audit row only and let the agent re-derive its
    own context via ``list_session_artifacts``. The seed_message
    integration is documented in M2.3's changelog as the next agent
    refinement.
    """
    started = time.perf_counter()
    _ = db

    config, summary, skip_reason = await _preflight(
        workspace_id=workspace_id,
        invocation_kind=invocation_kind,
        actor_identity_id=actor_identity_id,
        bypass_min_artifacts=bypass_min_artifacts,
    )
    if skip_reason is not None:
        return await _finalise_skip(
            workspace_id=workspace_id,
            engine="agent",
            invocation_kind=invocation_kind,
            actor_identity_id=actor_identity_id,
            summary=summary,
            skip_reason=skip_reason,
            started=started,
            config=config,
        )

    factory = get_session_factory()
    async with factory() as fresh_db:
        aux_config = await get_aux_model(
            fresh_db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW
        )
        summary_text = await summarize_drain_with_aux(
            fresh_db,
            workspace_id=workspace_id,
            summary=summary,
            aux_config=aux_config,
        )
    aux_model_name = aux_config.model if aux_config is not None else None

    # Lazy import — the agent module pulls in pydantic-ai which we
    # do not want at module-load time of the workflow service (which
    # also runs in tests that mock out the LLM stack).
    from app.agents.builtin.evolver_agent import (
        EvolverError,
        invoke_evolver_subagent,
    )

    proposals_created = 0
    error_message: str | None = None
    try:
        result = await invoke_evolver_subagent(
            workspace_id=workspace_id,
            triggering_run_ids=list(summary.sample_run_ids) or None,
            invocation_kind=invocation_kind,
            actor_identity_id=actor_identity_id,
        )
        proposals_created = int(result.proposals_created)
        if result.error:
            error_message = result.error
    except EvolverError as exc:
        error_message = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        error_message = f"{type(exc).__name__}: {exc}"
        log.exception(
            "evolver agent dispatch crashed for workspace=%s", workspace_id
        )

    duration_ms = int((time.perf_counter() - started) * 1000)

    if error_message is not None and proposals_created == 0:
        await _record_audit(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=AUDIT_FAILED,
            summary=f"evolver workflow ({invocation_kind}) agent dispatch failed",
            metadata={
                "engine": "agent",
                "invocation_kind": invocation_kind,
                "duration_ms": duration_ms,
                "drain": summary.to_metadata(),
                "stage": "evolve",
                "error": error_message,
                "aux_model": aux_model_name,
                "seed_message_chars": len(summary_text),
            },
        )
        return WorkflowExecutionResult(
            workspace_id=workspace_id,
            engine="agent",
            artifacts_drained=summary.artifact_count,
            artifacts_summarized=summary.artifact_count,
            proposals_created=proposals_created,
            skipped=False,
            skip_reason=None,
            duration_ms=duration_ms,
            error=error_message,
            invocation_kind=invocation_kind,
            aux_model=aux_model_name,
        )

    await _maybe_reset_breaker(
        workspace_id=workspace_id, proposals_created=proposals_created
    )
    await _record_audit(
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
        action=AUDIT_WORKFLOW_COMPLETED,
        summary=(
            f"evolver workflow ({invocation_kind}) completed via agent: "
            f"{proposals_created} proposal(s) from {summary.artifact_count} artifact(s)"
        ),
        metadata={
            "engine": "agent",
            "invocation_kind": invocation_kind,
            "duration_ms": duration_ms,
            "stages_succeeded": ["drain", "summarize", "evolve", "publish"],
            "artifacts_drained": int(summary.artifact_count),
            "artifacts_summarized": int(summary.artifact_count),
            "proposals_created": int(proposals_created),
            "drain": summary.to_metadata(),
            "aux_model": aux_model_name,
            "seed_message_chars": len(summary_text),
            "agent_error": error_message,
        },
    )
    return WorkflowExecutionResult(
        workspace_id=workspace_id,
        engine="agent",
        artifacts_drained=summary.artifact_count,
        artifacts_summarized=summary.artifact_count,
        proposals_created=proposals_created,
        skipped=False,
        skip_reason=None,
        duration_ms=duration_ms,
        error=error_message,
        invocation_kind=invocation_kind,
        aux_model=aux_model_name,
    )


# ─── Skip + dispatcher ──────────────────────────────────────
async def _finalise_skip(
    *,
    workspace_id: uuid.UUID,
    engine: Literal["workflow", "agent"],
    invocation_kind: str,
    actor_identity_id: uuid.UUID | None,
    summary: DrainSummary,
    skip_reason: str,
    started: float,
    config: EvolverSettings,
) -> WorkflowExecutionResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    await _record_audit(
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
        action=AUDIT_SKIPPED,
        summary=f"evolver workflow skipped: {skip_reason}",
        metadata={
            "workspace_id": str(workspace_id),
            "engine": engine,
            "invocation_kind": invocation_kind,
            "skip_reason": skip_reason,
            "drain": summary.to_metadata(),
            "min_artifacts_per_evolution": int(config.min_artifacts_per_evolution),
            "duration_ms": duration_ms,
        },
    )
    return WorkflowExecutionResult(
        workspace_id=workspace_id,
        engine=engine,
        artifacts_drained=summary.artifact_count,
        artifacts_summarized=summary.artifact_count,
        proposals_created=0,
        skipped=True,
        skip_reason=skip_reason,
        duration_ms=duration_ms,
        error=None,
        invocation_kind=invocation_kind,
        aux_model=None,
    )


async def evolve_workspace_skills(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    invocation_kind: Literal["scheduled", "manual"] = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
    bypass_min_artifacts: bool = False,
) -> WorkflowExecutionResult:
    """Dispatcher: read EvolverSettings.engine and route accordingly.

    Centralised so callers (the daily ARQ sweep, the manual trigger
    endpoint, internal tooling) stay engine-agnostic and only the
    workspace's persisted choice picks the implementation.
    """
    config = await get_workspace_evolver_config(db, workspace_id=workspace_id)
    if config.engine == "agent":
        return await evolve_workspace_skills_agent(
            db,
            workspace_id=workspace_id,
            invocation_kind=invocation_kind,
            actor_identity_id=actor_identity_id,
            bypass_min_artifacts=bypass_min_artifacts,
        )
    return await evolve_workspace_skills_workflow(
        db,
        workspace_id=workspace_id,
        invocation_kind=invocation_kind,
        actor_identity_id=actor_identity_id,
        bypass_min_artifacts=bypass_min_artifacts,
    )
