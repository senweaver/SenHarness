"""Skill usage rollup cron (M1.3).

Daily at 02:30 UTC. The job walks every non-deleted workspace and,
for each pack whose ``last_used_at`` is stale (or NULL), writes an
aggregate-derived ``last_used_at`` + ``effectiveness_avg`` based on
the previous 30 days of :class:`~app.db.models.skill_usage.SkillUsage`
rows.

Scheduling note: the slot 02:30 UTC is intentional. M0.11 retention
runs at minute={0,5,…} (every 5 minutes), the M0.10 cleanup runs at
03:30 UTC, and the M0.7 pending-memory sweep runs at minutes 2 and
32. 02:30 UTC therefore avoids every existing burst.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.security import utcnow_naive
from app.db.models.skills import SkillPack, SkillPackState
from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.services import skill_usage as skill_usage_svc

log = logging.getLogger(__name__)


_ROLLUP_WINDOW = timedelta(days=30)
# Packs whose ``last_used_at`` is fresher than this don't need a recompute
# this tick — the live record_usage path keeps them current already.
_FRESHNESS_WINDOW = timedelta(hours=24)


async def rollup_skill_usage(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron tick.

    For each workspace, pick every non-tombstoned pack with a stale
    ``last_used_at`` and recompute its 30-day rollup. Returns a JSON-
    serialisable summary so the operator can compare ticks in the
    arq dashboard / audit feed.

    The job is best-effort: a failure on one pack does not block the
    rest of the workspace, and a failure on one workspace does not
    block the rest of the platform.
    """
    factory = get_session_factory()
    now = utcnow_naive()
    since = now - _ROLLUP_WINDOW
    fresh_after = now - _FRESHNESS_WINDOW

    workspaces_processed = 0
    packs_updated = 0
    total_usage_rows_aggregated = 0

    async with factory() as db:
        ws_rows = (
            (await db.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None))))
            .scalars()
            .all()
        )

    for ws_id in ws_rows:
        workspaces_processed += 1
        async with factory() as db:
            stmt = select(SkillPack.id).where(
                SkillPack.workspace_id == ws_id,
                SkillPack.deleted_at.is_(None),
                SkillPack.state != SkillPackState.TOMBSTONE,
            )
            pack_ids = (await db.execute(stmt)).scalars().all()

        for pack_id in pack_ids:
            try:
                async with factory() as db:
                    stats = await skill_usage_svc.aggregate_pack_stats(
                        db, workspace_id=ws_id, pack_id=pack_id, since=since
                    )
                if stats["use_count"] == 0:
                    continue
                last_used_at = stats["last_used_at"]
                if last_used_at is not None and last_used_at >= fresh_after:
                    continue

                async with factory() as db:
                    await skill_usage_svc.update_pack_stats_from_usage(
                        db, workspace_id=ws_id, pack_id=pack_id, since=since
                    )
                    await db.commit()
                packs_updated += 1
                total_usage_rows_aggregated += int(stats["use_count"])
            except Exception:  # pragma: no cover - never let one pack tank the sweep
                log.exception("rollup_skill_usage failed for ws=%s pack=%s", ws_id, pack_id)

    return {
        "status": "ok",
        "workspaces_processed": workspaces_processed,
        "packs_updated": packs_updated,
        "total_usage_rows_aggregated": total_usage_rows_aggregated,
    }


__all__ = ["rollup_skill_usage"]
