"""Skill usage telemetry service (M1.3).

This module owns three responsibilities:

1. :func:`record_usage` and :func:`record_usage_batch` — workspace-
   scoped inserts that are *forgiving*: a missing pack does not raise
   so the M1.5 wiring path can be defensive about ordering when the
   runtime captures a pack id that has been concurrently archived.

2. :func:`aggregate_pack_stats` — read-only summary used by the
   ``/usage/stats`` endpoint and the ARQ rollup. The window is
   bounded by ``since`` so the caller picks the lookback (24 h for
   live UI, 30 d for the daily rollup, 7 d for the trend chart).

3. :func:`update_pack_stats_from_usage` — writes the aggregate back
   to ``SkillPack.last_used_at`` and ``SkillPack.effectiveness_avg``.
   Audits ``skill.stats_rolled_up`` once per pack so a periodic sweep
   is fully traceable.

Audit actions emitted:

* ``skill.usage_recorded`` — single-row record (low-volume admin /
  test path; the runtime path uses the batch variant below).
* ``skill.usage_batch_recorded`` — N-row batch (one audit per batch,
  metadata captures ``batch_size`` and the event kind).
* ``skill.stats_rolled_up`` — pack-level stats write.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack
from app.repositories.skill_usage import SkillUsageRepository
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ROLLUP_WINDOW",
    "STATS_TREND_WINDOW",
    "aggregate_pack_stats",
    "record_usage",
    "record_usage_batch",
    "update_pack_stats_from_usage",
]


DEFAULT_ROLLUP_WINDOW: timedelta = timedelta(days=30)
STATS_TREND_WINDOW: timedelta = timedelta(days=7)


async def _pack_in_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
) -> SkillPack | None:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        return None
    return pack


async def record_usage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    event_kind: SkillUsageEventKind,
    version_id: uuid.UUID | None = None,
    contribution_score: float | None = None,
) -> SkillUsage | None:
    """Insert one usage row.

    Returns ``None`` when the pack does not exist (or belongs to a
    different workspace) so callers from the runtime never have to
    catch an exception when a race archives the pack mid-run.
    """
    pack = await _pack_in_workspace(db, workspace_id=workspace_id, pack_id=pack_id)
    if pack is None:
        log.debug(
            "skill_usage.record skipped: pack %s not in workspace %s",
            pack_id,
            workspace_id,
        )
        return None

    row = await SkillUsageRepository(db).record(
        workspace_id=workspace_id,
        pack_id=pack_id,
        version_id=version_id,
        run_id=run_id,
        session_id=session_id,
        agent_id=agent_id,
        identity_id=identity_id,
        event_kind=event_kind,
        contribution_score=contribution_score,
    )
    await audit_svc.record(
        db,
        action="skill.usage_recorded",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack_id,
        summary=f"recorded {event_kind.value} on pack {pack.slug!r}",
        metadata={
            "event_kind": event_kind.value,
            "run_id": str(run_id),
            "session_id": str(session_id),
        },
    )
    return row


async def record_usage_batch(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
    event_kind: SkillUsageEventKind,
    pack_ids: list[uuid.UUID],
    version_ids: dict[uuid.UUID, uuid.UUID] | None = None,
) -> list[SkillUsage]:
    """Insert one row per ``pack_id`` for the same ``run_id``.

    Used by the M1.5 capture path so a single run that injects N skill
    packs writes N usage rows + one audit row (cost: O(1) audit per
    run regardless of injection breadth).

    Packs that don't belong to ``workspace_id`` are silently skipped —
    the M1.5 wiring path constructs ``pack_ids`` from the runtime's
    in-memory selection which can drift from DB state if a pack was
    archived during the run.
    """
    if not pack_ids:
        return []

    repo_skills = SkillPackRepository(db)
    repo_usage = SkillUsageRepository(db)
    rows: list[SkillUsage] = []
    skipped: list[str] = []
    for pid in pack_ids:
        pack = await repo_skills.get(pid, include_deleted=True)
        if pack is None or pack.workspace_id != workspace_id:
            skipped.append(str(pid))
            continue
        ver_id = (version_ids or {}).get(pid)
        row = await repo_usage.record(
            workspace_id=workspace_id,
            pack_id=pid,
            version_id=ver_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
            event_kind=event_kind,
            contribution_score=None,
        )
        rows.append(row)

    if rows:
        await audit_svc.record(
            db,
            action="skill.usage_batch_recorded",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="skill_run",
            resource_id=run_id,
            summary=f"recorded {len(rows)} {event_kind.value} usage rows for run",
            metadata={
                "event_kind": event_kind.value,
                "run_id": str(run_id),
                "session_id": str(session_id),
                "batch_size": len(rows),
                "skipped_count": len(skipped),
            },
        )
    return rows


async def aggregate_pack_stats(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    since: datetime,
) -> dict:
    """Return the aggregate dict from the repository (thin pass-through).

    Centralised here so callers don't have to instantiate the repository
    themselves; the same shape is used by the API ``/stats`` route, the
    ARQ rollup, and the unit tests.
    """
    return await SkillUsageRepository(db).aggregate_pack_stats(
        workspace_id=workspace_id,
        pack_id=pack_id,
        since=since,
    )


async def update_pack_stats_from_usage(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    since: datetime,
) -> SkillPack | None:
    """Write aggregate back to ``SkillPack`` + audit.

    Returns the updated :class:`SkillPack` row, or ``None`` if the pack
    does not exist in the workspace. The repository ``update`` helper
    refreshes the row so the caller can immediately serialise it
    without another roundtrip.
    """
    pack = await _pack_in_workspace(db, workspace_id=workspace_id, pack_id=pack_id)
    if pack is None:
        return None

    stats = await aggregate_pack_stats(
        db, workspace_id=workspace_id, pack_id=pack_id, since=since
    )
    last_used_at: datetime | None = stats["last_used_at"]
    contribution_avg: float | None = stats["contribution_avg"]

    updates: dict = {}
    if last_used_at is not None:
        updates["last_used_at"] = last_used_at
    if contribution_avg is not None:
        updates["effectiveness_avg"] = contribution_avg

    if updates:
        await SkillPackRepository(db).update(pack, **updates)

    await audit_svc.record(
        db,
        action="skill.stats_rolled_up",
        actor_identity_id=None,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack_id,
        summary=(
            f"rolled up {stats['use_count']} usage rows for pack {pack.slug!r}"
        ),
        metadata={
            "use_count": stats["use_count"],
            "last_used_at": (
                last_used_at.isoformat() if last_used_at is not None else None
            ),
            "contribution_avg": contribution_avg,
            "by_kind": stats["by_kind"],
            "since": since.isoformat(),
        },
    )
    return pack


def default_rollup_since(*, now: datetime | None = None) -> datetime:
    """Helper exposed for tests + the ARQ task.

    Centralises the 30-day lookback so changing the window in one
    place updates every caller.
    """
    return (now or utcnow_naive()) - DEFAULT_ROLLUP_WINDOW
