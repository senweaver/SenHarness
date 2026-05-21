"""Cross-session insights ARQ task (M4.5).

Runs once per ``/insights [--days N]`` invocation (slash command in
chat or REST POST ``/insights/generate``):

1. Pull the caller's session_artifacts in the workspace whose
   ``finished_at >= now - days``. Same-identity filter is the privacy
   gate per the M4.5 design.
2. Stratify the artifact set into clusters by judge_score = -1 /
   error_kind / dominant invoked tool. Empty backlog renders the
   "no insights yet" notice + early-returns.
3. Ask the aux LLM (task=SUMMARIZE → falls through SUMMARIZE → JUDGE
   → workspace default) to produce 5-7 :class:`InsightItem` rows. Aux
   failure short-circuits to a heuristic seed message and trips the
   shared evolver breaker so subsequent ``/insights`` invocations skip
   the call entirely until recovery.
4. Render the resulting :class:`InsightsResult` as a markdown
   ``assistant`` message in ``return_session_id`` and audit
   ``insights.cross_session_summarized``.

Per design point 3 the breaker bucket is shared with the evolver agent
(``EVOLVER_BREAKER_BUCKET``). A sick aux trips one bucket; degrading
silently would be dishonest.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
)
from app.agents.tools.skill_propose import EVOLVER_BREAKER_BUCKET
from app.core.security import utcnow_naive
from app.db.models.message import MessageRole
from app.db.models.session_artifact import SessionArtifact
from app.db.session import get_session_factory
from app.jobs._breaker import bump_failure, is_breaker_open, reset_failure
from app.repositories.session_artifact import SessionArtifactRepository
from app.services import audit as audit_svc
from app.services import session as session_svc

log = logging.getLogger(__name__)


__all__ = [
    "InsightCategory",
    "InsightItem",
    "InsightsResult",
    "generate_insights",
    "on_insights_job_failed_permanent",
    "render_insights_markdown",
]


# ─── Pydantic shapes ─────────────────────────────────────────
InsightCategory = Literal[
    "frequent_failure",
    "common_pattern",
    "skill_drift",
    "tool_overuse",
    "general",
]


class InsightItem(BaseModel):
    """One cluster the aux LLM extracted from the artifact backlog.

    ``evidence_session_ids`` ride straight into the markdown body as
    clickable links so the user can jump back to the supporting
    transcripts. The aux LLM is asked for ``evidence_artifact_ids``
    instead — the runtime resolves them to ``session_id`` via the
    artifact lookup table so a private artifact id never leaks into
    the chat surface.
    """

    title: str = Field(min_length=2, max_length=120)
    summary: str = Field(min_length=10, max_length=600)
    category: InsightCategory = "general"
    evidence_artifact_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=10
    )


class InsightsResult(BaseModel):
    items: list[InsightItem] = Field(default_factory=list, max_length=12)


# ─── Internal carriers (post-resolution) ─────────────────────
@dataclass(slots=True)
class _ResolvedItem:
    title: str
    summary: str
    category: InsightCategory
    evidence_session_ids: list[uuid.UUID] = field(default_factory=list)
    evidence_run_count: int = 0


@dataclass(slots=True)
class _ArtifactSnapshot:
    """Denormalised artifact row used for clustering + aux prompt build."""

    artifact_id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID
    final_outcome: str | None
    error_kind: str | None
    judge_score: float | None
    invoked_tools: list[str]
    injected_skill_pack_ids: list[str]
    iteration_count: int
    finished_at: datetime


# ─── Limits ──────────────────────────────────────────────────
# Aux prompt budget — keeps a 200-artifact backlog inside a typical
# small-model context. Doubled budget is cheap; halving cuts useful
# signal so this is intentionally generous.
_AUX_PROMPT_MAX_CHARS = 6000
_AUX_TIMEOUT_S = 35.0
# Breaker tunables. Window/recover mirror the judge bucket so a
# transient aux blip recovers within an hour.
_BREAKER_WINDOW_S = 300
_BREAKER_RECOVER_S = 3600
# Default strike count when EvolverSettings can't be resolved (fresh
# workspace with no platform default seeded). Matches the schema
# default of EvolverSettings.evolver_breaker_strikes.
_DEFAULT_BREAKER_STRIKES = 5


# ─── ARQ entrypoint ──────────────────────────────────────────
async def generate_insights(
    ctx: dict[str, Any],
    *,
    workspace_id: str,
    identity_id: str,
    return_session_id: str,
    days: int = 30,
) -> dict[str, Any]:
    """Generate + persist a markdown insights message.

    Returns a JSON-safe summary the ARQ result store keeps for ~24h
    so operators can grep for ``insights.cross_session_summarized``
    runs without joining against the audit table. Wraps every aux
    failure in the shared evolver breaker so consecutive sick calls
    fan out across all aux signals at once (per the M4.5 design).
    """
    _ = ctx
    started = time.perf_counter()
    ws_uuid = uuid.UUID(workspace_id)
    identity_uuid = uuid.UUID(identity_id)
    session_uuid = uuid.UUID(return_session_id)
    days = max(1, int(days))
    factory = get_session_factory()

    # 1. Pull artifact set under the identity privacy filter.
    artifacts = await _load_artifacts_for_identity(
        workspace_id=ws_uuid,
        identity_id=identity_uuid,
        days=days,
    )

    if not artifacts:
        await _post_no_insights_message(
            session_id=session_uuid,
            workspace_id=ws_uuid,
            days=days,
            identity_id=identity_uuid,
        )
        await _audit(
            workspace_id=ws_uuid,
            actor_identity_id=identity_uuid,
            action="insights.cross_session_summarized",
            session_id=session_uuid,
            summary=(
                f"insights generated for last {days}d — no artifacts in window"
            ),
            metadata={
                "days": days,
                "artifact_count": 0,
                "item_count": 0,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "trigger": "generate_insights",
            },
        )
        return {
            "status": "empty",
            "artifact_count": 0,
            "item_count": 0,
            "days": days,
            "session_id": str(session_uuid),
        }

    # 2. Breaker check — shared evolver bucket.
    breaker_open = await _is_aux_breaker_open(workspace_id=ws_uuid)

    items: list[_ResolvedItem]
    aux_model_used: str | None
    aux_skipped_reason: str | None

    if breaker_open:
        items = _heuristic_items(artifacts)
        aux_model_used = None
        aux_skipped_reason = "breaker_open"
        await _audit(
            workspace_id=ws_uuid,
            actor_identity_id=identity_uuid,
            action="insights.aux_skipped",
            session_id=session_uuid,
            summary="insights aux skipped — shared evolver breaker open",
            metadata={
                "days": days,
                "artifact_count": len(artifacts),
                "bucket": EVOLVER_BREAKER_BUCKET,
                "trigger": "generate_insights",
            },
        )
    else:
        # 3. Aux LLM call.
        items, aux_model_used, aux_skipped_reason = await _aux_summarise(
            workspace_id=ws_uuid, artifacts=artifacts, days=days
        )
        if aux_skipped_reason is None:
            await reset_failure(
                bucket=EVOLVER_BREAKER_BUCKET, workspace_id=str(ws_uuid)
            )
        else:
            await bump_failure(
                bucket=EVOLVER_BREAKER_BUCKET,
                workspace_id=str(ws_uuid),
                window_seconds=_BREAKER_WINDOW_S,
                recover_seconds=_BREAKER_RECOVER_S,
            )
            await _audit(
                workspace_id=ws_uuid,
                actor_identity_id=identity_uuid,
                action="insights.aux_skipped",
                session_id=session_uuid,
                summary=(
                    f"insights aux skipped — {aux_skipped_reason}"
                ),
                metadata={
                    "days": days,
                    "artifact_count": len(artifacts),
                    "reason": aux_skipped_reason,
                    "trigger": "generate_insights",
                },
            )

    # 4. Persist markdown assistant message + audit.
    body = render_insights_markdown(
        items=items,
        days=days,
        artifact_count=len(artifacts),
        aux_model=aux_model_used,
        degraded=aux_skipped_reason is not None,
    )
    persisted_message_id: uuid.UUID | None = None
    try:
        async with factory() as db:
            sess = await session_svc.get_session_or_404(
                db, session_uuid, workspace_id=ws_uuid
            )
            msg = await session_svc.append_message(
                db,
                session_obj=sess,
                role=MessageRole.ASSISTANT,
                content_json={"text": body},
                metadata_json={
                    "kind": "cross_session_insights",
                    "days": days,
                    "artifact_count": len(artifacts),
                    "item_count": len(items),
                    "aux_model": aux_model_used,
                    "degraded": aux_skipped_reason is not None,
                    "generated_for_identity_id": str(identity_uuid),
                },
            )
            await db.commit()
            persisted_message_id = msg.id
    except Exception:  # pragma: no cover
        log.exception(
            "insights message persist failed session=%s identity=%s",
            session_uuid,
            identity_uuid,
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    await _audit(
        workspace_id=ws_uuid,
        actor_identity_id=identity_uuid,
        action="insights.cross_session_summarized",
        session_id=session_uuid,
        summary=(
            f"insights generated for last {days}d "
            f"({len(items)} item(s) across {len(artifacts)} artifact(s))"
        ),
        metadata={
            "days": days,
            "artifact_count": len(artifacts),
            "item_count": len(items),
            "aux_model": aux_model_used,
            "degraded": aux_skipped_reason is not None,
            "skip_reason": aux_skipped_reason,
            "duration_ms": duration_ms,
            "message_id": (
                str(persisted_message_id) if persisted_message_id else None
            ),
            "trigger": "generate_insights",
        },
    )

    return {
        "status": "ok" if aux_skipped_reason is None else "degraded",
        "artifact_count": len(artifacts),
        "item_count": len(items),
        "days": days,
        "aux_model": aux_model_used,
        "degraded": aux_skipped_reason is not None,
        "session_id": str(session_uuid),
        "message_id": (
            str(persisted_message_id) if persisted_message_id else None
        ),
    }


# ─── Permanent-failure hook ──────────────────────────────────
async def on_insights_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """ARQ hook for ``generate_insights`` exhausting its retry budget.

    Mirrors the curator / evolver hooks: writes one stable audit row
    so operators can spot the dead-letter run without trawling Redis.
    Best-effort; never re-raises.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="insights.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(
                    f"generate_insights failed permanently: {exc!r}"
                ),
                metadata={
                    "function": str(
                        ctx.get("function") or "generate_insights"
                    ),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_insights_job_failed_permanent hook crashed")


# ─── Artifact loader ─────────────────────────────────────────
async def _load_artifacts_for_identity(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    days: int,
) -> list[_ArtifactSnapshot]:
    """Pull the caller's recent artifacts inside ``days``.

    Privacy gate: ``identity_id`` is the user whose history we're
    summarising and the only owner whose artifacts we touch. The
    workspace_id filter is redundant when paired with the identity
    filter (artifacts inherit workspace_id from their session) but is
    kept explicit so a future cross-workspace identity model still
    bounds the scan to the caller's tenant.

    Returns at most :data:`InsightsSettings.max_artifacts_per_summary`
    rows, newest-first. Cancelled / soft-deleted rows are excluded so
    the aux summariser never sees half-runs.
    """
    cutoff = utcnow_naive() - timedelta(days=days)
    factory = get_session_factory()
    cap = await _resolve_artifact_cap(workspace_id=workspace_id)

    async with factory() as db:
        repo = SessionArtifactRepository(db)
        rows = await repo.list_recent_for_workspace(
            workspace_id=workspace_id,
            since=cutoff,
            limit=int(cap),
        )

    snapshots: list[_ArtifactSnapshot] = []
    for art in rows:
        if art.identity_id != identity_id:
            continue
        if str(art.final_outcome or "").lower() == "cancelled":
            continue
        snapshots.append(
            _ArtifactSnapshot(
                artifact_id=art.id,
                session_id=art.session_id,
                run_id=art.run_id,
                final_outcome=art.final_outcome,
                error_kind=art.error_kind,
                judge_score=(
                    float(art.judge_score)
                    if art.judge_score is not None
                    else None
                ),
                invoked_tools=list(art.invoked_tools or []),
                injected_skill_pack_ids=[
                    str(s) for s in (art.injected_skill_pack_ids or [])
                ],
                iteration_count=int(art.iteration_count or 0),
                finished_at=art.finished_at,
            )
        )
    return snapshots


async def _resolve_artifact_cap(*, workspace_id: uuid.UUID) -> int:
    """Read ``InsightsSettings.max_artifacts_per_summary`` for the workspace."""
    factory = get_session_factory()
    try:
        async with factory() as db:
            from app.services.cross_session_insights import (
                get_workspace_insights_config,
            )

            cfg = await get_workspace_insights_config(
                db, workspace_id=workspace_id
            )
            return int(cfg.max_artifacts_per_summary)
    except Exception:  # pragma: no cover - defensive
        return 200


# ─── Breaker ─────────────────────────────────────────────────
async def _is_aux_breaker_open(*, workspace_id: uuid.UUID) -> bool:
    """Check the shared evolver breaker for this workspace."""
    factory = get_session_factory()
    trip_at = _DEFAULT_BREAKER_STRIKES
    try:
        async with factory() as db:
            from app.services.evolver_config import get_workspace_evolver_config

            cfg = await get_workspace_evolver_config(
                db, workspace_id=workspace_id
            )
            trip_at = max(1, int(cfg.evolver_breaker_strikes or trip_at))
    except Exception:  # pragma: no cover - fail-open on config read
        log.exception(
            "insights breaker trip_at lookup failed for ws=%s", workspace_id
        )
    return await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=trip_at,
    )


# ─── Heuristic / aux summariser ──────────────────────────────
def _category_for_label(label: str) -> InsightCategory:
    label = label.lower()
    if label.startswith("error:") or label.startswith("failure"):
        return "frequent_failure"
    if label.startswith("tool:"):
        return "tool_overuse"
    if label.startswith("skill:") or label.startswith("pack:"):
        return "skill_drift"
    return "common_pattern"


def _heuristic_items(
    artifacts: Sequence[_ArtifactSnapshot],
) -> list[_ResolvedItem]:
    """Deterministic clustering when aux LLM is unavailable.

    Buckets by ``error_kind`` first (frequent failures dominate the
    user's lived experience), then by dominant invoked tool, then by
    skill pack. Each bucket carries up to five evidence sessions so
    the markdown links don't sprawl. Buckets smaller than two are
    dropped — one isolated artifact is too thin for an "insight".
    """
    err_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    skill_counter: Counter[str] = Counter()
    by_error: dict[str, list[_ArtifactSnapshot]] = {}
    by_tool: dict[str, list[_ArtifactSnapshot]] = {}
    by_skill: dict[str, list[_ArtifactSnapshot]] = {}

    for art in artifacts:
        if art.error_kind:
            kind = str(art.error_kind)
            err_counter[kind] += 1
            by_error.setdefault(kind, []).append(art)
        for tool in art.invoked_tools:
            tool_counter[tool] += 1
            by_tool.setdefault(tool, []).append(art)
        for pack in art.injected_skill_pack_ids:
            skill_counter[pack] += 1
            by_skill.setdefault(pack, []).append(art)

    items: list[_ResolvedItem] = []

    for kind, count in err_counter.most_common(3):
        if count < 2:
            continue
        bucket = by_error.get(kind, [])
        items.append(
            _ResolvedItem(
                title=f"Recurring failure: {kind}",
                summary=(
                    f"You hit `{kind}` in {count} of the last "
                    f"{len(artifacts)} runs. Worth checking the "
                    "preconditions or the tool involved."
                ),
                category="frequent_failure",
                evidence_session_ids=_unique_sessions(bucket, limit=5),
                evidence_run_count=count,
            )
        )

    for tool, count in tool_counter.most_common(3):
        if count < 2:
            continue
        bucket = by_tool.get(tool, [])
        items.append(
            _ResolvedItem(
                title=f"Frequently used tool: {tool}",
                summary=(
                    f"`{tool}` was invoked across {count} runs. If the "
                    "calls look repetitive, a custom skill pack might "
                    "shorten the loop."
                ),
                category="tool_overuse",
                evidence_session_ids=_unique_sessions(bucket, limit=5),
                evidence_run_count=count,
            )
        )

    for pack, count in skill_counter.most_common(2):
        if count < 2:
            continue
        bucket = by_skill.get(pack, [])
        items.append(
            _ResolvedItem(
                title=f"Skill pack hot path: {pack}",
                summary=(
                    f"Pack `{pack}` was injected into {count} runs. "
                    "Heavy reliance suggests the pack body is "
                    "load-bearing — keep it pinned or curate it."
                ),
                category="skill_drift",
                evidence_session_ids=_unique_sessions(bucket, limit=5),
                evidence_run_count=count,
            )
        )

    if not items:
        # No clusters reached the size floor; surface a single neutral
        # observation so the user still gets *something* back.
        items.append(
            _ResolvedItem(
                title="Quiet stretch",
                summary=(
                    f"{len(artifacts)} run(s) in the lookback window "
                    "without a dominant failure mode or tool pattern. "
                    "Nothing to flag right now."
                ),
                category="general",
                evidence_session_ids=_unique_sessions(artifacts, limit=5),
                evidence_run_count=len(artifacts),
            )
        )
    return items[:7]


def _unique_sessions(
    artifacts: Sequence[_ArtifactSnapshot], *, limit: int
) -> list[uuid.UUID]:
    """Stable de-dup of session_ids preserving newest-first order."""
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for art in artifacts:
        if art.session_id in seen:
            continue
        seen.add(art.session_id)
        out.append(art.session_id)
        if len(out) >= limit:
            break
    return out


async def _aux_summarise(
    *,
    workspace_id: uuid.UUID,
    artifacts: Sequence[_ArtifactSnapshot],
    days: int,
) -> tuple[list[_ResolvedItem], str | None, str | None]:
    """Drive the aux LLM call. Returns ``(items, aux_model, skip_reason)``.

    ``skip_reason`` is ``None`` on success and a short string token
    otherwise so the caller can drive the audit / breaker bookkeeping
    without re-deriving the failure mode.
    """
    factory = get_session_factory()
    async with factory() as db:
        config = await _resolve_aux_config(db, workspace_id=workspace_id)

    if config is None:
        return _heuristic_items(artifacts), None, "no_aux_model"

    system = _aux_system_prompt(days=days)
    user_prompt = _build_aux_user_prompt(
        artifacts=artifacts, days=days, max_chars=_AUX_PROMPT_MAX_CHARS
    )
    response = await call_aux_chat(
        config=config,
        system=system,
        user=user_prompt,
        response_format=InsightsResult,
        timeout_s=_AUX_TIMEOUT_S,
    )
    if not isinstance(response, InsightsResult) or not response.items:
        return _heuristic_items(artifacts), config.model, "aux_failure"

    artifact_index = {art.artifact_id: art for art in artifacts}
    resolved: list[_ResolvedItem] = []
    for item in response.items:
        evidence_sessions: list[uuid.UUID] = []
        evidence_runs = 0
        seen: set[uuid.UUID] = set()
        for art_id in item.evidence_artifact_ids:
            art = artifact_index.get(art_id)
            if art is None:
                continue
            evidence_runs += 1
            if art.session_id in seen:
                continue
            seen.add(art.session_id)
            evidence_sessions.append(art.session_id)
        # Aux didn't supply evidence — degrade by tagging the most
        # recent artifacts so the user still gets clickable context.
        if not evidence_sessions:
            evidence_sessions = _unique_sessions(artifacts, limit=3)
            evidence_runs = max(1, len(evidence_sessions))
        resolved.append(
            _ResolvedItem(
                title=item.title.strip()[:120],
                summary=item.summary.strip()[:600],
                category=item.category,
                evidence_session_ids=evidence_sessions[:5],
                evidence_run_count=evidence_runs,
            )
        )
    return resolved[:7], config.model, None


async def _resolve_aux_config(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> AuxiliaryConfig | None:
    """Resolve the SUMMARIZE-task aux config; honours InsightsSettings.aux_model.

    The ``InsightsSettings.aux_model`` override (set per workspace via
    ``home_config_json["insights"]["aux_model"]``) takes precedence
    over the workspace's ``aux_model_summarize`` knob. When it's
    ``None`` we fall through to the SUMMARIZE → JUDGE → DEFAULT chain
    inside :func:`get_aux_model`.
    """
    from app.services.cross_session_insights import (
        get_workspace_insights_config,
    )

    cfg = await get_workspace_insights_config(db, workspace_id=workspace_id)
    if cfg.aux_model:
        # Build a one-shot AuxiliaryConfig directly so the override is
        # honoured even when the workspace ``aux_model_summarize``
        # field is empty.
        provider, _, model_name = cfg.aux_model.partition(":")
        if provider and model_name:
            return AuxiliaryConfig(
                task=AuxiliaryTask.SUMMARIZE,
                model=cfg.aux_model,
            )
    return await get_aux_model(
        db, workspace_id=workspace_id, task=AuxiliaryTask.SUMMARIZE
    )


def _aux_system_prompt(*, days: int) -> str:
    return (
        "You are a cross-session insights summariser. Read the structured "
        f"trace of one user's last {days} day(s) of agent runs and "
        "extract 3–7 concrete insights. Each insight must:\n"
        "* be specific (mention the failure shape, tool, or skill pack);\n"
        "* be actionable when possible;\n"
        "* cite at least one supporting artifact_id from the input.\n"
        "Categories: 'frequent_failure', 'common_pattern', 'skill_drift', "
        "'tool_overuse', 'general'. Use the user's language. Do not "
        "invent failure modes that the trace does not support."
    )


def _build_aux_user_prompt(
    *,
    artifacts: Sequence[_ArtifactSnapshot],
    days: int,
    max_chars: int,
) -> str:
    """Produce a deterministic compact dump of the artifact backlog."""
    err_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    score_dist: Counter[int] = Counter()

    for art in artifacts:
        if art.error_kind:
            err_counter[str(art.error_kind)] += 1
        for tool in art.invoked_tools:
            tool_counter[tool] += 1
        if art.judge_score is not None:
            score_dist[int(round(art.judge_score))] += 1

    header = (
        f"window_days={days} artifact_count={len(artifacts)} "
        f"score_distribution={dict(score_dist)} "
        f"top_error_kinds={err_counter.most_common(5)} "
        f"top_invoked_tools={tool_counter.most_common(5)}"
    )

    rows: list[str] = []
    for art in artifacts:
        line = (
            f"- artifact_id={art.artifact_id} "
            f"session_id={art.session_id} "
            f"outcome={art.final_outcome or '?'} "
            f"score={art.judge_score if art.judge_score is not None else 'na'} "
            f"err={art.error_kind or '-'} "
            f"iter={art.iteration_count} "
            f"tools={','.join(art.invoked_tools[:5]) or '-'}"
        )
        rows.append(line)

    body = header + "\nARTIFACTS:\n" + "\n".join(rows)
    if len(body) > max_chars:
        body = body[: max_chars - len("\n[truncated]")] + "\n[truncated]"
    return body


# ─── Markdown rendering ──────────────────────────────────────
_CATEGORY_LABELS: dict[InsightCategory, str] = {
    "frequent_failure": "Frequent failure",
    "common_pattern": "Common pattern",
    "skill_drift": "Skill drift",
    "tool_overuse": "Tool overuse",
    "general": "General",
}


def render_insights_markdown(
    *,
    items: Sequence[_ResolvedItem],
    days: int,
    artifact_count: int,
    aux_model: str | None,
    degraded: bool,
) -> str:
    """Render the markdown body persisted as the assistant message.

    Each item ends with one ``Evidence`` line containing markdown
    links of the form ``[Session abcd1234](/?session=<id>)``. The
    frontend already renders markdown links inside chat messages so
    no new component is required (per the M4.5 frontend brief).
    """
    if not items:
        return _empty_state_markdown(days=days, artifact_count=artifact_count)

    lines: list[str] = []
    lines.append(
        f"### Cross-session insights — last {days} day(s)"
    )
    summary_line = (
        f"Across **{artifact_count}** of your recent runs"
        if artifact_count
        else "No artifacts in the lookback window"
    )
    if aux_model:
        summary_line += f" · summarised by `{aux_model}`"
    if degraded:
        summary_line += " · _degraded fallback (aux unavailable)_"
    lines.append(summary_line + ".")
    lines.append("")

    for idx, item in enumerate(items, start=1):
        category_label = _CATEGORY_LABELS.get(
            item.category, _CATEGORY_LABELS["general"]
        )
        lines.append(f"**{idx}. {item.title}** _({category_label})_")
        lines.append(item.summary.strip())
        if item.evidence_session_ids:
            link_parts = []
            for sid in item.evidence_session_ids:
                short = str(sid).split("-", 1)[0]
                link_parts.append(f"[Session {short}](/?session={sid})")
            evidence = " · ".join(link_parts)
            lines.append(
                f"_Evidence ({item.evidence_run_count} run(s)):_ {evidence}"
            )
        lines.append("")

    lines.append(
        "_This summary was generated from your own session artifacts only "
        "(workspace-scoped, identity-scoped). Run `/insights --days N` to "
        "change the window._"
    )
    return "\n".join(lines).strip() + "\n"


def _empty_state_markdown(*, days: int, artifact_count: int) -> str:
    _ = artifact_count  # kept for signature symmetry; rendered text is constant
    return (
        f"### Cross-session insights — last {days} day(s)\n\n"
        "No insights yet — there are no completed agent runs in this "
        "window for your account. Try a longer lookback (e.g. "
        "`/insights --days 90`) or come back after a few more "
        "conversations.\n"
    )


# ─── Helpers ─────────────────────────────────────────────────
async def _post_no_insights_message(
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    days: int,
    identity_id: uuid.UUID,
) -> None:
    factory = get_session_factory()
    body = _empty_state_markdown(days=days, artifact_count=0)
    try:
        async with factory() as db:
            sess = await session_svc.get_session_or_404(
                db, session_id, workspace_id=workspace_id
            )
            await session_svc.append_message(
                db,
                session_obj=sess,
                role=MessageRole.ASSISTANT,
                content_json={"text": body},
                metadata_json={
                    "kind": "cross_session_insights",
                    "days": days,
                    "artifact_count": 0,
                    "item_count": 0,
                    "degraded": False,
                    "generated_for_identity_id": str(identity_id),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception(
            "no-insights message persist failed session=%s", session_id
        )


async def _audit(
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    action: str,
    session_id: uuid.UUID,
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
                resource_type="session",
                resource_id=session_id,
                summary=summary,
                metadata=metadata,
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("insights audit failed action=%s", action)


# Silence unused imports — kept for downstream re-export convenience.
_ = (datetime, UTC)
