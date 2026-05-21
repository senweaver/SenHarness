"""Evolver-only helper tools (M2.2).

Three read-side / control-flow tools the platform-builtin evolver
agent calls alongside the six M2.1 + M2.7 propose verbs:

* :func:`run_list_session_artifacts` — surfaces low-scoring runs the
  agent should review. Workspace-scoped, structural metadata only —
  no raw user_text ever leaves the artifact row.
* :func:`run_read_skill_pack` — fetches a pack's metadata + the
  ACTIVE version body (truncated) so the agent has the actual SKILL
  bytes when crafting a patch.
* :func:`run_mark_skip` — records "no proposals worth making" and
  returns ``stop=True`` so the loop in
  :mod:`app.agents.builtin.evolver_agent` exits cleanly.

All three are gated by ``available_for_kinds=("evolver",)`` in
:data:`app.agents.tools.BUILTIN_TOOL_REGISTRY`; non-evolver agents
never see them in their tool catalogue.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, get_context
from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.session import get_session_factory
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_MARKED_SKIP",
    "ListSessionArtifactsArgs",
    "MarkSkipArgs",
    "READ_SKILL_PACK_TRUNCATE_CHARS",
    "ReadSkillPackArgs",
    "run_list_session_artifacts",
    "run_mark_skip",
    "run_read_skill_pack",
]


# Truncate ACTIVE content_md so the agent sees a representative head
# slice without blowing the aux model context. M2 evolver patches
# operate on small old_text spans; 8 KiB is enough for a focused
# review pass and keeps the model honest about ranging the full pack.
READ_SKILL_PACK_TRUNCATE_CHARS = 8000

# Default upper bound on judge_score for "interesting" runs. -1.0 is
# the failure score; 0.0 keeps partial-success runs included so the
# agent can patch a skill that *almost* worked. Callers can tighten.
DEFAULT_JUDGE_SCORE_MAX = 0.0

AUDIT_MARKED_SKIP = "evolver.marked_skip"


# ─── list_session_artifacts ──────────────────────────────────
class ListSessionArtifactsArgs(BaseModel):
    """Window selector for the most recent low-scoring runs."""

    limit: int = Field(
        ge=1,
        le=50,
        default=10,
        description="Maximum number of artifacts to return.",
    )
    judge_score_max: float = Field(
        default=DEFAULT_JUDGE_SCORE_MAX,
        description=(
            "Upper bound on judge_score; default 0.0 surfaces failures "
            "and partials only. Pass 1.0 to include successes for a "
            "regression-detection sweep."
        ),
    )
    since_days: int = Field(
        ge=1,
        le=90,
        default=7,
        description="Only consider artifacts finished within this many days.",
    )


async def run_list_session_artifacts(
    args: ListSessionArtifactsArgs,
) -> dict[str, Any]:
    """Return recent workspace artifacts the evolver should review.

    The payload is intentionally minimal — id, run_id, finished_at,
    judge_score, error_kind, iteration_count, invoked_tools,
    final_outcome, injected_skill_pack_ids. Raw turns and user text
    never leave the DB through this path; the agent designs proposals
    from structural signals plus the SkillPack content it explicitly
    fetches via :func:`run_read_skill_pack`.
    """
    ctx = get_context()
    factory = get_session_factory()
    cutoff = utcnow_naive() - timedelta(days=int(args.since_days))
    async with factory() as db:
        stmt = (
            select(SessionArtifact)
            .where(
                SessionArtifact.workspace_id == ctx.workspace_id,
                SessionArtifact.deleted_at.is_(None),
                SessionArtifact.finished_at >= cutoff,
                SessionArtifact.judge_score.is_not(None),
                SessionArtifact.judge_score <= float(args.judge_score_max),
            )
            .order_by(SessionArtifact.finished_at.desc())
            .limit(int(args.limit))
        )
        rows = (await db.execute(stmt)).scalars().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "artifact_id": str(row.id),
                "run_id": str(row.run_id),
                "session_id": str(row.session_id),
                "finished_at": row.finished_at.isoformat(),
                "judge_score": (
                    float(row.judge_score) if row.judge_score is not None else None
                ),
                "goal_alignment_avg": (
                    float(row.goal_alignment_avg)
                    if row.goal_alignment_avg is not None
                    else None
                ),
                "error_kind_hint": row.error_kind,
                "final_outcome": row.final_outcome,
                "iteration_count": int(row.iteration_count or 0),
                "invoked_tools": list(row.invoked_tools or []),
                "injected_skill_pack_ids": list(row.injected_skill_pack_ids or []),
            }
        )
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
        "since_days": int(args.since_days),
        "judge_score_max": float(args.judge_score_max),
    }


# ─── read_skill_pack ─────────────────────────────────────────
class ReadSkillPackArgs(BaseModel):
    """Identifier of the pack to inspect."""

    pack_id: uuid.UUID


async def run_read_skill_pack(args: ReadSkillPackArgs) -> dict[str, Any]:
    """Return SkillPack metadata + ACTIVE content_md (truncated).

    Falls back to the legacy ``SkillFile['SKILL.md']`` row when the
    pack predates the M1.2 version table — same fallback the patch
    verb uses, so the agent reads the same bytes the patch verb will
    edit.
    """
    ctx = get_context()
    factory = get_session_factory()
    async with factory() as db:
        pack = await SkillPackRepository(db).get(args.pack_id, include_deleted=True)
        if pack is None or pack.workspace_id != ctx.workspace_id:
            return {
                "status": "rejected",
                "code": "evolver.pack_not_found",
                "message": f"Skill pack {args.pack_id} does not exist in this workspace.",
            }

        version_repo = SkillPackVersionRepository(db)
        active = await version_repo.get_active(
            workspace_id=ctx.workspace_id, pack_id=pack.id
        )
        if active is not None:
            content = active.content_md or ""
            version_no: int | None = int(active.version_no)
            content_hash: str | None = active.content_hash
            files = list((active.files_json or {}).keys())
        else:
            file_repo = SkillFileRepository(db)
            files_rows = await file_repo.list_for_pack(
                workspace_id=ctx.workspace_id, skill_pack_id=pack.id
            )
            skill_md = next((f for f in files_rows if f.path == "SKILL.md"), None)
            content = skill_md.content_md if skill_md is not None else ""
            version_no = None
            content_hash = None
            files = [f.path for f in files_rows]

    truncated = False
    if len(content) > READ_SKILL_PACK_TRUNCATE_CHARS:
        content = content[:READ_SKILL_PACK_TRUNCATE_CHARS]
        truncated = True

    return {
        "status": "ok",
        "pack_id": str(pack.id),
        "slug": pack.slug,
        "name": pack.name,
        "description": pack.description,
        "state": pack.state.value,
        "pinned": bool(pack.pinned),
        "active_version_no": version_no,
        "active_content_hash": content_hash,
        "files": files,
        "content_md": content,
        "content_truncated": truncated,
        "content_truncate_limit": READ_SKILL_PACK_TRUNCATE_CHARS,
    }


# ─── mark_skip ───────────────────────────────────────────────
class MarkSkipArgs(BaseModel):
    """Reason the agent has nothing worth proposing this invocation."""

    rationale: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Short justification, e.g. 'failures look like model noise; "
            "all artifacts already have well-scoped SKILL.md bodies.'"
        ),
    )


async def run_mark_skip(args: MarkSkipArgs) -> dict[str, Any]:
    """Record an intentional skip + signal the loop to halt.

    The factory always opens a fresh session because the evolver agent
    runs outside a chat session and never carries an open db handle on
    the ToolRunContext. Returning ``stop=True`` is the contract the
    M2.2 :func:`invoke_evolver_subagent` loop reads to break out
    without further model rounds.
    """
    ctx = get_context()
    factory = get_session_factory()
    async with factory() as db:
        await audit_svc.record(
            db,
            action=AUDIT_MARKED_SKIP,
            actor_identity_id=ctx.identity_id,
            workspace_id=ctx.workspace_id,
            resource_type="workspace",
            resource_id=ctx.workspace_id,
            summary="evolver decided no proposals are worth making",
            metadata={
                "run_id": str(ctx.run_id),
                "rationale": args.rationale,
            },
        )
        await db.commit()
    return {
        "status": "skipped",
        "stop": True,
        "rationale": args.rationale,
        "final_message": (
            "No SkillPack proposals worth filing this round. " + args.rationale
        ),
    }


# Keep ToolRunContext referenced so ``ty`` doesn't flag the alias as
# unused; the type itself is exercised via :func:`get_context` only.
_: type = ToolRunContext
