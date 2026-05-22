"""Gateway-message repository.

Operates on the ``gateway_messages`` queue. Concurrency-sensitive ops use
``FOR UPDATE SKIP LOCKED`` so multiple worker replicas can poll without
stepping on each other.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, asc, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models.gateway_message import (
    GatewayMessage,
    GatewayMessageDirection,
    GatewayMessageStatus,
)
from app.db.repository import AsyncRepository


class GatewayRepository(AsyncRepository[GatewayMessage]):
    model = GatewayMessage

    # ─── Request side (SenHarness → worker) ──────────────────
    async def enqueue_request(
        self,
        *,
        workspace_id: uuid.UUID,
        adapter_id: uuid.UUID,
        run_id: uuid.UUID,
        session_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        payload: dict[str, Any],
        seq: int = 0,
        kind: str = "run",
    ) -> GatewayMessage:
        return await self.create(
            workspace_id=workspace_id,
            adapter_id=adapter_id,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            direction=GatewayMessageDirection.REQUEST,
            kind=kind,
            seq=seq,
            payload_json=payload,
            status=GatewayMessageStatus.PENDING,
        )

    async def claim_pending_requests(
        self,
        *,
        adapter_id: uuid.UUID,
        limit: int,
        now: datetime,
    ) -> Sequence[GatewayMessage]:
        """Grab up to ``limit`` pending requests + cancel events for delivery.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` semantics via two round-trips
        (``select`` + ``update``); Postgres upgrades the row locks atomically.
        """

        stmt = (
            select(GatewayMessage)
            .where(
                GatewayMessage.adapter_id == adapter_id,
                GatewayMessage.direction == GatewayMessageDirection.REQUEST,
                GatewayMessage.status == GatewayMessageStatus.PENDING,
            )
            .order_by(asc(GatewayMessage.created_at), asc(GatewayMessage.seq))
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return []

        for row in rows:
            row.status = GatewayMessageStatus.DELIVERED
            row.claimed_at = now
        await self.session.flush(rows)
        return rows

    async def cancel_pending_for_run(self, *, run_id: uuid.UUID) -> int:
        """Mark still-undelivered request rows as FAILED (after a cancel)."""

        stmt = (
            update(GatewayMessage)
            .where(
                GatewayMessage.run_id == run_id,
                GatewayMessage.direction == GatewayMessageDirection.REQUEST,
                GatewayMessage.status.in_(
                    [
                        GatewayMessageStatus.PENDING,
                        GatewayMessageStatus.DELIVERED,
                    ]
                ),
            )
            .values(status=GatewayMessageStatus.FAILED)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def mark_run_terminal(self, *, run_id: uuid.UUID, status: GatewayMessageStatus) -> int:
        """Flip any outstanding request rows for a terminated run."""

        stmt = (
            update(GatewayMessage)
            .where(
                GatewayMessage.run_id == run_id,
                GatewayMessage.direction == GatewayMessageDirection.REQUEST,
                GatewayMessage.status != GatewayMessageStatus.ACKED,
            )
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    # ─── Event side (worker → SenHarness) ────────────────────
    async def append_event(
        self,
        *,
        workspace_id: uuid.UUID,
        adapter_id: uuid.UUID,
        run_id: uuid.UUID,
        session_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        kind: str,
        seq: int,
        data: dict[str, Any],
    ) -> tuple[GatewayMessage | None, bool]:
        """Insert an event. Returns ``(row, duplicated)`` — the unique
        ``(run_id, direction, seq)`` constraint collapses retries quietly."""

        try:
            row = await self.create(
                workspace_id=workspace_id,
                adapter_id=adapter_id,
                run_id=run_id,
                session_id=session_id,
                agent_id=agent_id,
                direction=GatewayMessageDirection.EVENT,
                kind=kind,
                seq=seq,
                payload_json=data,
                status=GatewayMessageStatus.EMITTED,
            )
            return row, False
        except IntegrityError:
            await self.session.rollback()
            return None, True

    async def list_events_since(
        self,
        *,
        run_id: uuid.UUID,
        after_seq: int,
        limit: int = 64,
    ) -> Sequence[GatewayMessage]:
        """Fetch EVENT rows with ``seq > after_seq`` ordered for replay."""

        stmt = (
            select(GatewayMessage)
            .where(
                GatewayMessage.run_id == run_id,
                GatewayMessage.direction == GatewayMessageDirection.EVENT,
                GatewayMessage.seq > after_seq,
            )
            .order_by(asc(GatewayMessage.seq), asc(GatewayMessage.created_at))
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def ack_run_if_terminal(self, *, run_id: uuid.UUID, kind: str) -> bool:
        """When the worker emits a ``final`` or ``error`` event, flip the
        originating request row to ``ACKED`` so stats show closure."""

        if kind not in {"final", "error"}:
            return False

        stmt = (
            update(GatewayMessage)
            .where(
                GatewayMessage.run_id == run_id,
                GatewayMessage.direction == GatewayMessageDirection.REQUEST,
                GatewayMessage.status.in_(
                    [
                        GatewayMessageStatus.PENDING,
                        GatewayMessageStatus.DELIVERED,
                    ]
                ),
            )
            .values(status=GatewayMessageStatus.ACKED)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def list_for_run(
        self,
        *,
        run_id: uuid.UUID,
        direction: GatewayMessageDirection | None = None,
    ) -> Sequence[GatewayMessage]:
        stmt = select(GatewayMessage).where(GatewayMessage.run_id == run_id)
        if direction is not None:
            stmt = stmt.where(GatewayMessage.direction == direction)
        stmt = stmt.order_by(
            asc(GatewayMessage.direction),
            asc(GatewayMessage.seq),
            asc(GatewayMessage.created_at),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def enqueue_cancel_event(
        self,
        *,
        workspace_id: uuid.UUID,
        adapter_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> GatewayMessage:
        """Push a synthetic ``cancel`` request row so the next poll informs
        the remote worker that it should abort the run."""

        # Use negative-ish sequence space to keep these cancel requests out of
        # the (run_id, request, seq=0) slot used by the real run. Each cancel
        # gets a fresh seq via wall-clock microseconds which is monotonic
        # enough for a single run's lifecycle.
        now = datetime.utcnow()
        cancel_seq = int(now.timestamp() * 1000) % 1_000_000 + 1_000_000
        # Ensure a unique seq if two cancels land in the same millisecond.
        existing = await self.session.execute(
            select(GatewayMessage.seq).where(
                and_(
                    GatewayMessage.run_id == run_id,
                    GatewayMessage.direction == GatewayMessageDirection.REQUEST,
                )
            )
        )
        seen = {int(s or 0) for s in existing.scalars().all()}
        while cancel_seq in seen:
            cancel_seq += 1

        return await self.create(
            workspace_id=workspace_id,
            adapter_id=adapter_id,
            run_id=run_id,
            session_id=None,
            agent_id=None,
            direction=GatewayMessageDirection.REQUEST,
            kind="cancel",
            seq=cancel_seq,
            payload_json={"run_id": str(run_id)},
            status=GatewayMessageStatus.PENDING,
        )
