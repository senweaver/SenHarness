"""Agent + AgentStar + AgentVersion repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, func, nulls_last, select

from app.db.models.agent import Agent, AgentVisibility
from app.db.models.agent_star import AgentStar
from app.db.models.agent_version import AgentVersion
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.repository import AsyncRepository


class AgentRepository(AsyncRepository[Agent]):
    model = Agent

    async def get_default_for_workspace(self, *, workspace_id: uuid.UUID) -> Agent | None:
        """Default agent for API shims (oldest non-deleted row)."""
        stmt = (
            select(Agent)
            .where(Agent.workspace_id == workspace_id, Agent.deleted_at.is_(None))
            .order_by(Agent.created_at.asc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_visible(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Agent]:
        """List agents the user can see (workspace + public + own private)."""
        stmt = (
            select(Agent)
            .where(Agent.workspace_id == workspace_id)
            .where(Agent.deleted_at.is_(None))
            .order_by(desc(Agent.created_at))
            .offset(offset)
            .limit(limit)
        )
        # TODO(P3): further restrict by visibility / created_by when visibility=private
        _ = identity_id
        return (await self.session.execute(stmt)).scalars().all()

    async def recent_for_identity(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        limit: int = 5,
    ) -> list[tuple[Agent, datetime | None, int, bool, bool]]:
        """Return (agent, last_message_at, message_count, starred, pinned).

        Sort: pinned desc, last_message_at desc, message_count desc, created_at desc.
        Falls back to no-telemetry rows once recent sessions are exhausted.
        """
        star_sq = (
            select(AgentStar.agent_id, AgentStar.pinned)
            .where(AgentStar.identity_id == identity_id)
            .subquery("star_sq")
        )
        session_stats = (
            select(
                SessionModel.subject_id.label("agent_id"),
                func.max(SessionModel.last_message_at).label("last_at"),
                func.coalesce(func.sum(SessionModel.message_count), 0).label("msg_count"),
            )
            .where(
                SessionModel.workspace_id == workspace_id,
                SessionModel.kind == SessionKind.P2P,
                SessionModel.owner_identity_id == identity_id,
                SessionModel.deleted_at.is_(None),
                SessionModel.subject_id.is_not(None),
            )
            .group_by(SessionModel.subject_id)
            .subquery("stats_sq")
        )

        stmt = (
            select(
                Agent,
                session_stats.c.last_at,
                session_stats.c.msg_count,
                star_sq.c.agent_id.is_not(None).label("starred"),
                func.coalesce(star_sq.c.pinned, False).label("pinned"),
            )
            .outerjoin(session_stats, session_stats.c.agent_id == Agent.id)
            .outerjoin(star_sq, star_sq.c.agent_id == Agent.id)
            .where(Agent.workspace_id == workspace_id, Agent.deleted_at.is_(None))
            .order_by(
                desc(func.coalesce(star_sq.c.pinned, False)),
                nulls_last(desc(session_stats.c.last_at)),
                desc(session_stats.c.msg_count),
                desc(Agent.created_at),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1], int(row[2] or 0), bool(row[3]), bool(row[4])) for row in rows]

    async def list_public_for_discovery(
        self,
        *,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        template_only: bool = False,
        limit: int = 40,
        offset: int = 0,
    ) -> list[tuple[Agent, int]]:
        """Public agents across **all** workspaces, with a star count.

        Sort: star count desc, then created_at desc (newer first for ties).

        Optional filters:
          * ``q`` — case-insensitive substring match on name + description.
          * ``category`` — exact match on ``metadata_json.category`` (the
            17 built-in template categories from
            :mod:`app.agents.templates.catalog`).
          * ``tag`` — string membership in ``metadata_json.tags``
            (Postgres JSONB ``?`` operator).
          * ``template_only`` — restrict to vendored templates
            (``metadata_json.template = true``).
        """
        from sqlalchemy import type_coerce
        from sqlalchemy.dialects.postgresql import JSONB

        star_count = (
            select(
                AgentStar.agent_id.label("agent_id"),
                func.count(AgentStar.id).label("stars"),
            )
            .group_by(AgentStar.agent_id)
            .subquery("star_count_sq")
        )

        stmt = (
            select(Agent, func.coalesce(star_count.c.stars, 0).label("stars"))
            .outerjoin(star_count, star_count.c.agent_id == Agent.id)
            .where(
                Agent.visibility == AgentVisibility.PUBLIC.value,
                Agent.deleted_at.is_(None),
            )
        )
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where((Agent.name.ilike(like)) | (Agent.description.ilike(like)))
        if category:
            stmt = stmt.where(Agent.metadata_json["category"].astext == category)
        if tag:
            # ``tags @> '["foo"]'``  — true when the JSONB array contains
            # the literal tag string.
            stmt = stmt.where(Agent.metadata_json["tags"].contains(type_coerce([tag], JSONB)))
        if template_only:
            stmt = stmt.where(Agent.metadata_json["template"] == type_coerce(True, JSONB))
        stmt = (
            stmt.order_by(
                desc(func.coalesce(star_count.c.stars, 0)),
                desc(Agent.created_at),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], int(row[1] or 0)) for row in rows]

    async def count_by_category(self, *, template_only: bool = False) -> dict[str, int]:
        """Count public agents per ``metadata_json.category``.

        Used by ``GET /agents/discover/categories`` to render the
        sidebar with live counts. NULL categories are dropped.

        When ``template_only`` is set, only vendored templates
        (``metadata_json.template = true``) are counted, so the count
        matches a template-only listing (e.g. the create-agent dialog).

        Implementation note: a subquery is used so the JSONB extraction
        renders exactly once. Without this, SQLAlchemy emits two
        parameterized ``->> $n`` calls and Postgres complains the
        ``GROUP BY`` expression doesn't match the ``SELECT`` one.
        """
        from sqlalchemy import type_coerce
        from sqlalchemy.dialects.postgresql import JSONB

        base = select(
            Agent.metadata_json["category"].astext.label("cat"),
            Agent.id.label("id"),
        ).where(
            Agent.visibility == AgentVisibility.PUBLIC.value,
            Agent.deleted_at.is_(None),
        )
        if template_only:
            base = base.where(Agent.metadata_json["template"] == type_coerce(True, JSONB))
        sub = base.subquery("public_cat_sq")
        stmt = (
            select(sub.c.cat, func.count(sub.c.id))
            .where(sub.c.cat.is_not(None))
            .group_by(sub.c.cat)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: int(row[1] or 0) for row in rows if row[0]}

    async def starred_for_identity(
        self, *, workspace_id: uuid.UUID, identity_id: uuid.UUID
    ) -> list[Agent]:
        stmt = (
            select(Agent)
            .join(AgentStar, AgentStar.agent_id == Agent.id)
            .where(
                Agent.workspace_id == workspace_id,
                Agent.deleted_at.is_(None),
                AgentStar.identity_id == identity_id,
            )
            .order_by(desc(AgentStar.pinned), desc(AgentStar.created_at))
        )
        return list((await self.session.execute(stmt)).scalars().all())


class AgentStarRepository(AsyncRepository[AgentStar]):
    model = AgentStar

    async def get_for(self, identity_id: uuid.UUID, agent_id: uuid.UUID) -> AgentStar | None:
        return await self.get_by(identity_id=identity_id, agent_id=agent_id)


class AgentVersionRepository(AsyncRepository[AgentVersion]):
    model = AgentVersion

    async def latest_version(self, agent_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(AgentVersion.version), 0)).where(
            AgentVersion.agent_id == agent_id
        )
        return int((await self.session.execute(stmt)).scalar() or 0)
