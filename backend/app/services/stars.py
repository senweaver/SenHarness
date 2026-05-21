"""Star fan-out helpers.

A row in ``agent_stars`` / ``squad_stars`` represents "this identity has
this item in their *My* list". The default for every workspace member
is **all workspace agents + squads, pinned=true**. Unpin = delete row,
re-pin = re-insert.

To keep that default true even as agents/squads are created and members
join, three call sites fan out the orthogonal dimension whenever one
side changes:

* ``services/agent.create_agent`` → seed the new agent across all
  current workspace members.
* ``services/squad.create_squad`` → same for a new squad.
* ``services/workspace.accept_invitation`` → seed every existing visible
  workspace agent + squad to the new member.

Inserts use ``ON CONFLICT DO NOTHING`` so the helpers are idempotent —
re-running never duplicates rows and never overwrites a user-set
``pinned`` value. Sessions are not fanned out; they remain
user-curated.
"""

from __future__ import annotations

import uuid

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.agent_star import AgentStar
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.squad import Squad
from app.db.models.squad_star import SquadStar


async def fan_out_agent_to_workspace_members(
    db: AsyncSession, *, workspace_id: uuid.UUID, agent_id: uuid.UUID
) -> None:
    """Insert a pinned star row for ``agent_id`` per active workspace member."""
    src = (
        select(
            Membership.identity_id.label("identity_id"),
            literal(agent_id).label("agent_id"),
            literal(True).label("pinned"),
        )
        .where(
            Membership.workspace_id == workspace_id,
            Membership.deleted_at.is_(None),
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    stmt = (
        pg_insert(AgentStar)
        .from_select(
            ["identity_id", "agent_id", "pinned"], src, include_defaults=False
        )
        .on_conflict_do_nothing(
            index_elements=["identity_id", "agent_id"],
        )
    )
    await db.execute(stmt)


async def fan_out_squad_to_workspace_members(
    db: AsyncSession, *, workspace_id: uuid.UUID, squad_id: uuid.UUID
) -> None:
    """Insert a pinned star row for ``squad_id`` per active workspace member."""
    src = (
        select(
            Membership.identity_id.label("identity_id"),
            literal(squad_id).label("squad_id"),
            literal(workspace_id).label("workspace_id"),
            literal(True).label("pinned"),
        )
        .where(
            Membership.workspace_id == workspace_id,
            Membership.deleted_at.is_(None),
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    stmt = (
        pg_insert(SquadStar)
        .from_select(
            ["identity_id", "squad_id", "workspace_id", "pinned"],
            src,
            include_defaults=False,
        )
        .on_conflict_do_nothing(
            index_elements=["identity_id", "squad_id"],
        )
    )
    await db.execute(stmt)


async def fan_out_workspace_to_member(
    db: AsyncSession, *, workspace_id: uuid.UUID, identity_id: uuid.UUID
) -> None:
    """Insert pinned star rows for every visible workspace agent + squad."""
    agent_src = (
        select(
            literal(identity_id).label("identity_id"),
            Agent.id.label("agent_id"),
            literal(True).label("pinned"),
        )
        .where(
            Agent.workspace_id == workspace_id,
            Agent.deleted_at.is_(None),
        )
    )
    agent_stmt = (
        pg_insert(AgentStar)
        .from_select(
            ["identity_id", "agent_id", "pinned"],
            agent_src,
            include_defaults=False,
        )
        .on_conflict_do_nothing(
            index_elements=["identity_id", "agent_id"],
        )
    )
    await db.execute(agent_stmt)

    squad_src = (
        select(
            literal(identity_id).label("identity_id"),
            Squad.id.label("squad_id"),
            literal(workspace_id).label("workspace_id"),
            literal(True).label("pinned"),
        )
        .where(
            Squad.workspace_id == workspace_id,
            Squad.deleted_at.is_(None),
        )
    )
    squad_stmt = (
        pg_insert(SquadStar)
        .from_select(
            ["identity_id", "squad_id", "workspace_id", "pinned"],
            squad_src,
            include_defaults=False,
        )
        .on_conflict_do_nothing(
            index_elements=["identity_id", "squad_id"],
        )
    )
    await db.execute(squad_stmt)
