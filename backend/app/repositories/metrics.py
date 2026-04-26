"""Usage / cost metrics repository — aggregates over the Message table.

Cost + token rollups live inside ``Message.token_usage_json`` (see
``app.api.v1.sessions._build_usage_json``). We aggregate ASSISTANT messages
only — user / tool messages don't consume LLM tokens.

All queries are workspace-scoped and (optionally) date-scoped. ``date_trunc``
runs in the DB timezone (set via ``APP_TIMEZONE`` / Postgres ``timezone``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Integer,
    Numeric,
    String,
    and_,
    cast,
    desc,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.message import Message, MessageRole


# Shared expression: cast JSONB number fields to proper SQL types.
def _int_field(name: str):
    return cast(Message.token_usage_json[name].astext, Integer)


def _num_field(name: str):
    return cast(Message.token_usage_json[name].astext, Numeric)


def _text_field(name: str):
    return cast(Message.token_usage_json[name].astext, String)


class MetricsRepository:
    """Aggregation queries for the /metrics/usage endpoints."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ─── helpers ─────────────────────────────────────────────
    @staticmethod
    def _base_filters(
        workspace_id: uuid.UUID,
        *,
        since: datetime | None,
        until: datetime | None,
        identity_id: uuid.UUID | None = None,
    ):
        # Only assistant turns that actually consumed tokens — we store a
        # non-empty ``token_usage_json`` with at least ``input``/``output``.
        conds = [
            Message.workspace_id == workspace_id,
            Message.role == MessageRole.ASSISTANT,
            or_(
                Message.token_usage_json["input"].astext.isnot(None),
                Message.token_usage_json["output"].astext.isnot(None),
            ),
        ]
        if since is not None:
            conds.append(Message.created_at >= since)
        if until is not None:
            conds.append(Message.created_at < until)
        if identity_id is not None:
            conds.append(Message.author_identity_id == identity_id)
        return and_(*conds)

    # ─── summary card ────────────────────────────────────────
    async def summary(
        self,
        *,
        workspace_id: uuid.UUID,
        since: datetime | None,
        until: datetime | None,
        identity_id: uuid.UUID | None = None,
    ) -> dict:
        stmt = select(
            func.coalesce(func.sum(_int_field("input")), 0).label("input_tokens"),
            func.coalesce(func.sum(_int_field("output")), 0).label("output_tokens"),
            func.coalesce(func.sum(_num_field("cost")), 0).label("cost_usd"),
            func.count(Message.id).label("turns"),
            func.count(func.distinct(Message.session_id)).label("sessions"),
            func.coalesce(func.avg(_int_field("latency_ms")), 0).label(
                "avg_latency_ms"
            ),
        ).where(
            self._base_filters(
                workspace_id, since=since, until=until, identity_id=identity_id
            )
        )
        row = (await self.session.execute(stmt)).one()
        return {
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
            "cost_usd": float(row.cost_usd or 0.0),
            "turns": int(row.turns or 0),
            "sessions": int(row.sessions or 0),
            "avg_latency_ms": float(row.avg_latency_ms or 0.0),
        }

    # ─── time series (per day) ───────────────────────────────
    async def daily(
        self,
        *,
        workspace_id: uuid.UUID,
        since: datetime,
        until: datetime,
        identity_id: uuid.UUID | None = None,
    ) -> list[dict]:
        bucket = func.date_trunc("day", Message.created_at).label("bucket")
        stmt = (
            select(
                bucket,
                func.coalesce(func.sum(_int_field("input")), 0).label("input_tokens"),
                func.coalesce(func.sum(_int_field("output")), 0).label(
                    "output_tokens"
                ),
                func.coalesce(func.sum(_num_field("cost")), 0).label("cost_usd"),
                func.count(Message.id).label("turns"),
            )
            .where(
                self._base_filters(
                    workspace_id, since=since, until=until, identity_id=identity_id
                )
            )
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "date": r.bucket.date().isoformat() if r.bucket else None,
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd": float(r.cost_usd or 0.0),
                "turns": int(r.turns or 0),
            }
            for r in rows
            if r.bucket is not None
        ]

    # ─── top agents ──────────────────────────────────────────
    async def top_agents(
        self,
        *,
        workspace_id: uuid.UUID,
        since: datetime | None,
        until: datetime | None,
        identity_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[dict]:
        stmt = (
            select(
                Message.author_agent_id.label("agent_id"),
                Agent.name.label("agent_name"),
                func.coalesce(func.sum(_int_field("input")), 0).label("input_tokens"),
                func.coalesce(func.sum(_int_field("output")), 0).label(
                    "output_tokens"
                ),
                func.coalesce(func.sum(_num_field("cost")), 0).label("cost_usd"),
                func.count(Message.id).label("turns"),
            )
            .join(Agent, Agent.id == Message.author_agent_id, isouter=True)
            .where(
                self._base_filters(
                    workspace_id, since=since, until=until, identity_id=identity_id
                ),
                Message.author_agent_id.isnot(None),
            )
            .group_by(Message.author_agent_id, Agent.name)
            .order_by(desc("cost_usd"), desc("turns"))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "agent_id": str(r.agent_id) if r.agent_id else None,
                "agent_name": r.agent_name,
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd": float(r.cost_usd or 0.0),
                "turns": int(r.turns or 0),
            }
            for r in rows
        ]

    # ─── top models ──────────────────────────────────────────
    async def top_models(
        self,
        *,
        workspace_id: uuid.UUID,
        since: datetime | None,
        until: datetime | None,
        identity_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[dict]:
        model = _text_field("model").label("model")
        provider = _text_field("provider").label("provider")
        stmt = (
            select(
                model,
                provider,
                func.coalesce(func.sum(_int_field("input")), 0).label("input_tokens"),
                func.coalesce(func.sum(_int_field("output")), 0).label(
                    "output_tokens"
                ),
                func.coalesce(func.sum(_num_field("cost")), 0).label("cost_usd"),
                func.count(Message.id).label("turns"),
            )
            .where(
                self._base_filters(
                    workspace_id, since=since, until=until, identity_id=identity_id
                )
            )
            .group_by(model, provider)
            .order_by(desc("cost_usd"), desc("turns"))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "model": r.model or "(unknown)",
                "provider": r.provider or "(unknown)",
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd": float(r.cost_usd or 0.0),
                "turns": int(r.turns or 0),
            }
            for r in rows
        ]
