"""Sidebar aggregator service.

Unions the caller's starred Agents, Squads and Sessions in the active
workspace into a single ordered list backing ``GET /api/v1/sidebar/my-items``.

Visibility is row-based: the list reflects whatever star rows exist for
``(identity_id, *)``. Defaults are seeded by
:mod:`app.services.stars._fan_out` family at agent / squad / member
creation time, plus a one-shot alembic backfill for pre-existing
workspaces. The user controls their list by deleting (unstar) or
inserting (re-star) rows; the service makes no implicit fan-out
decisions here.

Visibility rules:
    * Every star row carries ``workspace_id``, so the filter on the active
      workspace alone guarantees no cross-workspace leakage.
    * Soft-deleted target rows (Agent / Squad / Session ``deleted_at``)
      are excluded — we don't surface tombstones in the sidebar.

Sort: ``pinned DESC, (unread_count > 0) DESC, last_activity_at DESC NULLS LAST``.

``unread_count`` is a stub (always 0) for Phase 1 — Messages have no
per-identity read marker yet, so a real value would require a Spec-2
schema change. The wire shape is final.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.agent_star import AgentStar
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.models.session_star import SessionStar
from app.db.models.squad import Squad
from app.db.models.squad_star import SquadStar
from app.schemas.sidebar import SidebarItem, SidebarItemsResponse

log = logging.getLogger(__name__)


def _avatar_seed(name: str) -> str:
    stripped = (name or "").strip()
    return stripped[:1] if stripped else "·"


async def _agent_items(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> list[SidebarItem]:
    activity = (
        select(
            SessionModel.subject_id.label("agent_id"),
            func.max(SessionModel.last_message_at).label("last_at"),
        )
        .where(
            SessionModel.workspace_id == workspace_id,
            SessionModel.kind == SessionKind.P2P,
            SessionModel.deleted_at.is_(None),
            SessionModel.subject_id.is_not(None),
            SessionModel.owner_identity_id == identity_id,
        )
        .group_by(SessionModel.subject_id)
        .subquery("agent_activity")
    )
    stmt = (
        select(
            Agent.id,
            Agent.name,
            AgentStar.pinned,
            activity.c.last_at,
        )
        .join(AgentStar, AgentStar.agent_id == Agent.id)
        .outerjoin(activity, activity.c.agent_id == Agent.id)
        .where(
            AgentStar.identity_id == identity_id,
            Agent.workspace_id == workspace_id,
            Agent.deleted_at.is_(None),
        )
    )
    rows = (await db.execute(stmt)).all()
    return [
        SidebarItem(
            type="agent",
            id=row.id,
            name=row.name,
            avatar_seed=_avatar_seed(row.name),
            pinned=bool(row.pinned),
            unread_count=0,
            last_activity_at=row.last_at,
            href=f"/agents/{row.id}",
        )
        for row in rows
    ]


async def _squad_items(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> list[SidebarItem]:
    activity = (
        select(
            SessionModel.subject_id.label("squad_id"),
            func.max(SessionModel.last_message_at).label("last_at"),
        )
        .where(
            SessionModel.workspace_id == workspace_id,
            SessionModel.kind == SessionKind.SQUAD,
            SessionModel.deleted_at.is_(None),
            SessionModel.subject_id.is_not(None),
            SessionModel.owner_identity_id == identity_id,
        )
        .group_by(SessionModel.subject_id)
        .subquery("squad_activity")
    )
    stmt = (
        select(
            Squad.id,
            Squad.name,
            SquadStar.pinned,
            activity.c.last_at,
        )
        .join(SquadStar, SquadStar.squad_id == Squad.id)
        .outerjoin(activity, activity.c.squad_id == Squad.id)
        .where(
            SquadStar.identity_id == identity_id,
            SquadStar.workspace_id == workspace_id,
            Squad.workspace_id == workspace_id,
            Squad.deleted_at.is_(None),
        )
    )
    rows = (await db.execute(stmt)).all()
    return [
        SidebarItem(
            type="squad",
            id=row.id,
            name=row.name,
            avatar_seed=_avatar_seed(row.name),
            pinned=bool(row.pinned),
            unread_count=0,
            last_activity_at=row.last_at,
            href=f"/squads/{row.id}",
        )
        for row in rows
    ]


async def _session_items(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
) -> list[SidebarItem]:
    stmt = (
        select(
            SessionModel.id,
            SessionModel.title,
            SessionModel.last_message_at,
            SessionStar.pinned,
        )
        .join(SessionStar, SessionStar.session_id == SessionModel.id)
        .where(
            SessionStar.identity_id == identity_id,
            SessionStar.workspace_id == workspace_id,
            SessionModel.workspace_id == workspace_id,
            SessionModel.deleted_at.is_(None),
        )
    )
    rows = (await db.execute(stmt)).all()
    items: list[SidebarItem] = []
    for row in rows:
        name = (row.title or "").strip() or "Untitled session"
        items.append(
            SidebarItem(
                type="session",
                id=row.id,
                name=name,
                avatar_seed=_avatar_seed(name),
                pinned=bool(row.pinned),
                unread_count=0,
                last_activity_at=row.last_message_at,
                href=f"/sessions/{row.id}",
            )
        )
    return items


async def list_my_items(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    limit: int = 50,
) -> SidebarItemsResponse:
    """Return the caller's "我的" sidebar list, capped to ``limit``.

    The total count reflects pre-truncation size so the UI can decide
    whether to surface the inline search (>=12 items).
    """
    bucket = await _agent_items(db, workspace_id=workspace_id, identity_id=identity_id)
    bucket.extend(await _squad_items(db, workspace_id=workspace_id, identity_id=identity_id))
    bucket.extend(await _session_items(db, workspace_id=workspace_id, identity_id=identity_id))

    bucket.sort(
        key=lambda item: (
            0 if item.pinned else 1,
            0 if item.unread_count > 0 else 1,
            -(item.last_activity_at.timestamp() if item.last_activity_at else 0.0),
        )
    )

    total = len(bucket)
    return SidebarItemsResponse(items=bucket[:limit], total=total)
