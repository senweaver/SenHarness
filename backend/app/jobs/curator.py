"""Skill Curator nightly sweep + approval-handler ARQ tasks (M1.4).

Schedule
--------

The Curator runs once daily at **03:15 UTC**. The slot is wedged into
the dead zone between the existing nightly schedule:

* M0.11 retention sweep — every 5 min on the hour
* M1.3 skill usage rollup — 02:30 UTC
* M0.7 pending-memory sweep — minute {2, 32}
* M0.10 cleanup — 03:30 UTC
* M0.11 retention purge — 04:00 UTC

03:15 UTC has no contender; the only crons within ±10 min are the
on-the-hour 5-min retention sweep at 03:15 itself, which is fine
because both jobs scope to disjoint tables.

Job behaviour (per workspace)
-----------------------------

1. Read the workspace Curator config (``CuratorConfig``).
2. Skip the workspace entirely when ``config.enabled is False``.
3. **Stale sweep** — for each ACTIVE pack older than
   ``stale_after_days`` AND idle longer than ``min_idle_hours``,
   call :func:`skill_lifecycle.transition`
   ``target_state=STALE, actor_kind="curator", bypass_pinned=False``.
   Pinned packs raise :class:`PackPinnedAutoSkipped` which we swallow
   + count.
4. **Archive proposal** — for each STALE pack older than
   ``archive_after_days``, call
   :func:`skill_curator.propose_archive`. Pinned packs are skipped
   here too (the proposal would be vacuous because the apply step
   would also be blocked).
5. **Audit** — one ``curator.swept`` row per workspace with the
   summary; per-pack actions land their own audit lines via the
   transition / propose helpers.

Failure isolation
-----------------

Per-pack errors are caught + logged + counted; the sweep continues to
the next pack. Per-workspace errors are caught + logged + counted; the
sweep continues to the next workspace. Only an outer crash (e.g.
``get_session_factory`` itself raises) bubbles to ARQ so the
worker's ``max_tries=3`` retry budget protects against transient
infra blips.

Apply-on-approval
-----------------

:func:`curator_apply_approved` is the bridge from the approvals API
to the lifecycle transition. It is enqueued by the API decision
handler when the approved row's ``resource_type`` is
``skill_pack_archive``; it transitions the pack to ARCHIVED with
``actor_kind="curator"`` and writes ``curator.archived`` audit.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.skills import SkillPack, SkillPackState
from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.repositories.skill_usage import SkillUsageRepository
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc
from app.services import skill_curator as curator_svc
from app.services import skill_lifecycle as lifecycle_svc

log = logging.getLogger(__name__)

__all__ = [
    "CURATOR_APPLY_ARCHIVED",
    "CURATOR_TICK_NAME",
    "curator_apply_approved",
    "curator_propose_archive",
    "curator_tick",
]


CURATOR_TICK_NAME = "curator_tick"
CURATOR_APPLY_ARCHIVED = "curator.archived"


# ── Cron entrypoint ─────────────────────────────────────────
async def curator_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily Curator sweep across every non-deleted workspace.

    Returns a JSON-serialisable summary. The shape doubles as the
    arq-result-store body; the operator can compare ticks in the
    audit feed without trawling logs.
    """
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "status": "ok",
        "workspaces_seen": 0,
        "workspaces_disabled": 0,
        "workspaces_failed": 0,
        "stale_transitioned": 0,
        "stale_skipped_pinned": 0,
        "archive_proposed": 0,
        "archive_skipped_pinned": 0,
        "archive_skipped_existing": 0,
    }

    async with factory() as db:
        ws_rows = (
            (await db.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None))))
            .scalars()
            .all()
        )

    for ws_id in ws_rows:
        summary["workspaces_seen"] += 1
        try:
            ws_summary = await _curator_sweep_one_workspace(workspace_id=ws_id)
        except Exception:  # never let one workspace tank the cron
            log.exception("curator_tick failed for workspace=%s", ws_id)
            summary["workspaces_failed"] += 1
            continue
        if ws_summary.get("status") == "disabled":
            summary["workspaces_disabled"] += 1
            continue
        for key in (
            "stale_transitioned",
            "stale_skipped_pinned",
            "archive_proposed",
            "archive_skipped_pinned",
            "archive_skipped_existing",
        ):
            summary[key] += int(ws_summary.get(key, 0) or 0)

    return summary


# ── Per-workspace sweep (also reused by ``trigger_curator_now``) ──
async def _curator_sweep_one_workspace(*, workspace_id: uuid.UUID) -> dict[str, Any]:
    """One workspace's slice of the Curator sweep.

    Returned dict shape:

    * ``status`` — ``"ok"``, ``"disabled"`` or ``"workspace_missing"``
    * ``workspace_id`` — string
    * ``stale_transitioned`` / ``stale_skipped_pinned``
    * ``archive_proposed`` / ``archive_skipped_pinned`` /
      ``archive_skipped_existing``
    """
    factory = get_session_factory()
    now = utcnow_naive()

    async with factory() as db:
        config = await curator_svc.get_workspace_curator_config(db, workspace_id=workspace_id)
    if not config.enabled:
        return {
            "status": "disabled",
            "workspace_id": str(workspace_id),
            "stale_transitioned": 0,
            "stale_skipped_pinned": 0,
            "archive_proposed": 0,
            "archive_skipped_pinned": 0,
            "archive_skipped_existing": 0,
        }

    stale_transitioned = 0
    stale_skipped_pinned = 0
    archive_proposed = 0
    archive_skipped_pinned = 0
    archive_skipped_existing = 0

    # ── Step 1: ACTIVE → STALE ─────────────────────────────
    async with factory() as db:
        candidates = await curator_svc.find_stale_candidates(
            db,
            workspace_id=workspace_id,
            stale_after_days=config.stale_after_days,
            min_idle_hours=config.min_idle_hours,
            now=now,
        )

    for pack in candidates:
        try:
            async with factory() as db:
                try:
                    await lifecycle_svc.transition(
                        db,
                        pack_id=pack.id,
                        workspace_id=workspace_id,
                        target_state=SkillPackState.STALE,
                        actor_identity_id=None,
                        reason=(f"curator: idle for >= {config.stale_after_days} days"),
                        bypass_pinned=False,
                        actor_kind="curator",
                    )
                    await db.commit()
                    stale_transitioned += 1
                except lifecycle_svc.PackPinnedAutoSkipped:
                    # Lifecycle wrote the
                    # ``skill.transition_skipped_pinned`` audit row
                    # before raising; commit (not rollback) so it
                    # survives. No state mutation happened; only the
                    # audit row needs to land.
                    await db.commit()
                    stale_skipped_pinned += 1
                except lifecycle_svc.InvalidStateTransition:
                    # Race: rollup raced with capture and the pack
                    # left ACTIVE between candidate selection and the
                    # transition attempt. Drop silently — next tick
                    # will reconsider it.
                    await db.rollback()
        except Exception:  # one bad pack can't take the workspace down
            log.exception(
                "curator stale step failed ws=%s pack=%s",
                workspace_id,
                pack.id,
            )

    # ── Step 2: STALE → archive proposal ──────────────────
    async with factory() as db:
        archive_candidates = await curator_svc.find_archive_candidates(
            db,
            workspace_id=workspace_id,
            archive_after_days=config.archive_after_days,
            now=now,
        )

    for pack in archive_candidates:
        if pack.pinned:
            archive_skipped_pinned += 1
            continue
        try:
            use_count_30d = await _use_count_last_30d(
                workspace_id=workspace_id, pack_id=pack.id, now=now
            )
            async with factory() as db:
                # Re-fetch inside this session because find_… ran on
                # a separate session whose `pack` object is detached.
                fresh = (
                    await db.execute(select(SkillPack).where(SkillPack.id == pack.id))
                ).scalar_one_or_none()
                if fresh is None or fresh.workspace_id != workspace_id:
                    continue
                if fresh.state != SkillPackState.STALE:
                    # Race: pack flipped state after candidate scan.
                    continue
                approval = await curator_svc.propose_archive(
                    db,
                    workspace_id=workspace_id,
                    pack=fresh,
                    reason=(f"curator: stale for >= {config.archive_after_days} days"),
                    use_count_30d=use_count_30d,
                    now=now,
                )
                if approval is None:
                    archive_skipped_existing += 1
                else:
                    archive_proposed += 1
                await db.commit()
        except Exception:
            log.exception(
                "curator archive step failed ws=%s pack=%s",
                workspace_id,
                pack.id,
            )

    # ── Step 3: per-workspace summary audit ───────────────
    summary = {
        "status": "ok",
        "workspace_id": str(workspace_id),
        "stale_transitioned": stale_transitioned,
        "stale_skipped_pinned": stale_skipped_pinned,
        "archive_proposed": archive_proposed,
        "archive_skipped_pinned": archive_skipped_pinned,
        "archive_skipped_existing": archive_skipped_existing,
    }
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=curator_svc.CURATOR_SWEPT,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="workspace",
                resource_id=workspace_id,
                summary=(
                    f"curator swept: stale→{stale_transitioned} "
                    f"(skipped pinned {stale_skipped_pinned}); "
                    f"archive proposals {archive_proposed} "
                    f"(skipped pinned {archive_skipped_pinned}, "
                    f"existing {archive_skipped_existing})"
                ),
                metadata={
                    "stale_after_days": config.stale_after_days,
                    "archive_after_days": config.archive_after_days,
                    "min_idle_hours": config.min_idle_hours,
                    "active_skills_soft_cap": config.active_skills_soft_cap,
                    **{k: v for k, v in summary.items() if k != "workspace_id" and k != "status"},
                },
            )
            await db.commit()
    except Exception:
        log.exception("curator.swept audit failed for ws=%s", workspace_id)

    return summary


async def _use_count_last_30d(*, workspace_id: uuid.UUID, pack_id: uuid.UUID, now: Any) -> int:
    """Count of SkillUsage rows for ``pack_id`` in the last 30 days.

    Soft-fails on missing skill_usage table (pre-M1.3 deployments)
    or any DB error — the count is metadata for the proposal body
    only; missing it never blocks the Curator.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            stats = await SkillUsageRepository(db).aggregate_pack_stats(
                workspace_id=workspace_id,
                pack_id=pack_id,
                since=now - timedelta(days=30),
            )
            return int(stats.get("use_count", 0) or 0)
    except Exception:
        return 0


# ── Single-pack proposal entry (used by tests + admin tooling) ──
async def curator_propose_archive(
    ctx: dict[str, Any],
    pack_id: str,
    reason: str,
    actor_identity_id: str | None = None,
) -> dict[str, Any]:
    """File an archive proposal for one pack outside of the cron loop.

    Reserved for the M1.9 admin "Archive this stale pack" affordance
    and for end-to-end tests that need to drive the propose path
    deterministically. Returns ``{"status": "proposed"|"already_pending",
    "approval_id": <uuid|None>}``.
    """
    factory = get_session_factory()
    pid = uuid.UUID(str(pack_id))
    actor_uuid = uuid.UUID(actor_identity_id) if actor_identity_id else None

    async with factory() as db:
        pack = (await db.execute(select(SkillPack).where(SkillPack.id == pid))).scalar_one_or_none()
        if pack is None or pack.deleted_at is not None:
            return {"status": "skipped_missing", "approval_id": None}
        approval = await curator_svc.propose_archive(
            db,
            workspace_id=pack.workspace_id,
            pack=pack,
            reason=reason,
            actor_identity_id=actor_uuid,
        )
        if approval is None:
            await db.rollback()
            return {"status": "already_pending", "approval_id": None}
        await db.commit()
        return {"status": "proposed", "approval_id": str(approval.id)}


# ── Apply-on-approval ───────────────────────────────────────
async def curator_apply_approved(
    ctx: dict[str, Any],
    approval_id: str,
) -> dict[str, Any]:
    """Transition a pack to ARCHIVED after the approval is approved.

    Called by the approvals API after an admin approves a row whose
    ``resource_type == 'skill_pack_archive'``. Idempotent: if the row
    is no longer APPROVED, or the pack is already ARCHIVED /
    TOMBSTONE, the function returns a non-error status without
    re-triggering the transition.
    """
    factory = get_session_factory()
    aid = uuid.UUID(str(approval_id))

    async with factory() as db:
        approval = (
            await db.execute(select(Approval).where(Approval.id == aid))
        ).scalar_one_or_none()
        if approval is None:
            return {"status": "skipped_missing"}
        if approval.status != ApprovalStatus.APPROVED:
            return {"status": "skipped_not_approved", "current_status": approval.status}
        if approval.resource_type != ApprovalResourceType.SKILL_PACK_ARCHIVE.value:
            return {"status": "skipped_wrong_kind", "resource_type": approval.resource_type}
        if approval.resource_id is None:
            return {"status": "skipped_no_resource"}

        workspace_id = approval.workspace_id
        pack_id = approval.resource_id
        actor = approval.decided_by_identity_id

        pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
        if pack is None or pack.workspace_id != workspace_id:
            return {"status": "skipped_pack_missing"}
        if pack.state in (SkillPackState.ARCHIVED, SkillPackState.TOMBSTONE):
            return {"status": "noop_already_archived", "current_state": pack.state}

        # Pinned packs are exempt from automatic flows. Even after an
        # explicit approval the lifecycle gate refuses unless the
        # caller passes ``bypass_pinned=True``. The right policy here
        # is to honour the pin: the admin who approved the proposal
        # may not be aware the pack was pinned in the meantime.
        # Surface the skip so the caller's audit feed shows it.
        try:
            await lifecycle_svc.transition(
                db,
                pack_id=pack_id,
                workspace_id=workspace_id,
                target_state=SkillPackState.ARCHIVED,
                actor_identity_id=actor,
                reason=f"curator: approved archive (approval={aid})",
                bypass_pinned=False,
                actor_kind="curator",
            )
        except lifecycle_svc.PackPinnedAutoSkipped:
            await db.rollback()
            return {
                "status": "skipped_pinned",
                "approval_id": str(aid),
                "pack_id": str(pack_id),
            }

        await audit_svc.record(
            db,
            action=CURATOR_APPLY_ARCHIVED,
            actor_identity_id=actor,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=pack_id,
            summary=(f"Curator archived skill pack {pack.slug!r} after approval"),
            metadata={
                "approval_id": str(aid),
                "pack_id": str(pack_id),
                "slug": pack.slug,
            },
        )
        await db.commit()
        return {
            "status": "archived",
            "approval_id": str(aid),
            "pack_id": str(pack_id),
        }
