"""Skill usage telemetry API (M1.3).

Three routes — read recent rows, read aggregated stats, force a manual
rollup. The first two are workspace-member level; the rollup endpoint
is admin-only because it triggers a write back to ``SkillPack``.

The runtime path that *creates* usage rows is M1.5; this module only
exposes the read surface plus a debug-grade trigger.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.core.security import utcnow_naive
from app.db.models.skill_usage import SkillUsageEventKind
from app.repositories.skill_usage import SkillUsageRepository
from app.repositories.skills import SkillPackRepository
from app.schemas.skill_usage import (
    SkillUsageList,
    SkillUsageRead,
    SkillUsageRollupResult,
    SkillUsageStats,
)
from app.services import audit as audit_svc
from app.services import skill_usage as skill_usage_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/skills/packs", tags=["skills"])


_USAGE_READ = Depends(rate_limit("skill_usage_read", 60, 60))
_USAGE_ADMIN = Depends(rate_limit("skill_usage_admin", 5, 300))

# Live UI window: anything more recent skews to the live record path.
_LIVE_WINDOW = timedelta(days=30)


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _ensure_pack(db, *, workspace_id: uuid.UUID, pack_id: uuid.UUID) -> None:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")


@router.get(
    "/{pack_id}/usage",
    response_model=SkillUsageList,
    dependencies=[_USAGE_READ],
)
async def list_skill_usage(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    event_kind: Annotated[SkillUsageEventKind | None, Query()] = None,
) -> SkillUsageList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack(db, workspace_id=ws_id, pack_id=pack_id)

    rows = await SkillUsageRepository(db).list_for_pack(
        workspace_id=ws_id,
        pack_id=pack_id,
        limit=limit,
        event_kind=event_kind,
    )
    return SkillUsageList(
        pack_id=pack_id,
        items=[SkillUsageRead.model_validate(r) for r in rows],
    )


@router.get(
    "/{pack_id}/usage/stats",
    response_model=SkillUsageStats,
    dependencies=[_USAGE_READ],
)
async def get_skill_usage_stats(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    window_days: Annotated[int, Query(ge=1, le=180)] = 30,
) -> SkillUsageStats:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack(db, workspace_id=ws_id, pack_id=pack_id)

    now = utcnow_naive()
    since = now - timedelta(days=window_days)
    stats = await skill_usage_svc.aggregate_pack_stats(
        db, workspace_id=ws_id, pack_id=pack_id, since=since
    )

    trend_since = now - skill_usage_svc.STATS_TREND_WINDOW
    trend_stats = await skill_usage_svc.aggregate_pack_stats(
        db, workspace_id=ws_id, pack_id=pack_id, since=trend_since
    )

    return SkillUsageStats(
        pack_id=pack_id,
        window_days=window_days,
        use_count=stats["use_count"],
        last_used_at=stats["last_used_at"],
        contribution_avg=stats["contribution_avg"],
        use_count_by_kind=stats["by_kind"],
        trend_7d=trend_stats["by_kind"],
    )


@router.post(
    "/{pack_id}/usage/rollup",
    response_model=SkillUsageRollupResult,
    dependencies=[_USAGE_ADMIN],
)
async def trigger_skill_usage_rollup(
    pack_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillUsageRollupResult:
    """Manual single-pack rollup.

    Synchronous (does not enqueue an ARQ job) so the admin sees the
    fresh stats immediately. The daily ARQ task remains the canonical
    bulk path.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await _ensure_pack(db, workspace_id=ws_id, pack_id=pack_id)

    since = skill_usage_svc.default_rollup_since()
    pack = await skill_usage_svc.update_pack_stats_from_usage(
        db, workspace_id=ws_id, pack_id=pack_id, since=since
    )
    await audit_svc.record(
        db,
        action="skill.stats_rolled_up",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="skill_pack",
        resource_id=pack_id,
        summary="manual rollup triggered via API",
        metadata={"trigger": "api", "since": since.isoformat()},
        request=request,
    )
    await db.commit()

    stats = await skill_usage_svc.aggregate_pack_stats(
        db, workspace_id=ws_id, pack_id=pack_id, since=since
    )
    return SkillUsageRollupResult(
        pack_id=pack_id,
        last_used_at=pack.last_used_at if pack else None,
        effectiveness_avg=pack.effectiveness_avg if pack else None,
        use_count=stats["use_count"],
        rolled_up_at=utcnow_naive(),
    )
