"""Services backing the OpenClaw gateway (/api/v1/gw/openclaw/*).

Keeps the route layer thin: the FastAPI endpoints just validate input, call
one of the coroutines here, and serialize the result back. Long-poll
behaviour lives in :func:`poll_pending_requests`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.backend_adapter import BackendAdapter, BackendAdapterHealth
from app.db.models.gateway_message import (
    GatewayMessage,
    GatewayMessageDirection,
    GatewayMessageStatus,
)
from app.db.session import get_session_factory
from app.repositories.backend_adapter import BackendAdapterRepository
from app.repositories.gateway import GatewayRepository

log = logging.getLogger(__name__)


# ─── API key hashing ──────────────────────────────────────
def hash_api_key(raw: str) -> str:
    """SHA-256 hex digest — what we index on the hot auth path."""

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Raw X-Api-Key shown to the user exactly once.

    Length is 48 urlsafe-base64 chars ≈ 36 bytes of entropy. Enough entropy to
    fill SHA-256 without collisions in practice; short enough to paste.
    """

    return secrets.token_urlsafe(36)


# ─── Adapter lookup ───────────────────────────────────────
async def authenticate_adapter(
    session: AsyncSession, *, raw_api_key: str
) -> BackendAdapter | None:
    digest = hash_api_key(raw_api_key)
    repo = BackendAdapterRepository(session)
    return await repo.find_by_api_key_hash(digest)


async def mark_seen(
    session: AsyncSession,
    *,
    adapter: BackendAdapter,
    health: BackendAdapterHealth = BackendAdapterHealth.HEALTHY,
    now: datetime | None = None,
    capabilities: dict[str, Any] | None = None,
    endpoint: str | None = None,
) -> BackendAdapter:
    repo = BackendAdapterRepository(session)
    patch: dict[str, Any] = {
        "last_seen_at": now or utcnow_naive(),
        "health_status": health,
    }
    if capabilities is not None:
        patch["capabilities_json"] = capabilities
    if endpoint is not None:
        patch["endpoint"] = endpoint
    return await repo.update(adapter, **patch)


# ─── Poll path ────────────────────────────────────────────
async def poll_pending_requests(
    *,
    adapter_id: uuid.UUID,
    max_messages: int,
    wait_ms: int,
) -> Sequence[GatewayMessage]:
    """Long-poll the queue for an adapter.

    Opens its own short-lived DB session so we don't pin a connection for the
    full ``wait_ms`` window. Between polls we ``asyncio.sleep`` off the pool.
    """

    factory = get_session_factory()
    deadline = asyncio.get_event_loop().time() + max(0, wait_ms) / 1000.0
    poll_interval = max(
        0.05, min(0.5, max(0, wait_ms) / 1000.0 / 8 or 0.25)
    )

    while True:
        async with factory() as db:
            rows = await GatewayRepository(db).claim_pending_requests(
                adapter_id=adapter_id,
                limit=max_messages,
                now=utcnow_naive(),
            )
            if rows:
                await db.commit()
                return rows
            # No rows — don't hold the session while we sleep.

        if asyncio.get_event_loop().time() >= deadline:
            return []
        await asyncio.sleep(poll_interval)


# ─── Emit path ────────────────────────────────────────────
async def emit_event(
    session: AsyncSession,
    *,
    adapter: BackendAdapter,
    run_id: uuid.UUID,
    seq: int,
    kind: str,
    data: dict[str, Any],
) -> tuple[GatewayMessage | None, bool, bool]:
    """Record a worker-emitted event. Returns ``(row, duplicated, terminal)``.

    ``terminal`` is True when ``kind in {"final","error"}`` so the caller can
    flip the originating request row to ``ACKED`` and update stats.
    """

    repo = GatewayRepository(session)

    # Scope the emit to a run owned by the same adapter. Pull any row first so
    # we can learn workspace / session / agent; if none exists, the worker is
    # posting to a fabricated run_id and we refuse.
    existing = await repo.list_for_run(run_id=run_id)
    adapter_rows = [r for r in existing if r.adapter_id == adapter.id]
    if not adapter_rows:
        return None, False, False

    template = adapter_rows[0]
    row, duplicated = await repo.append_event(
        workspace_id=template.workspace_id,
        adapter_id=adapter.id,
        run_id=run_id,
        session_id=template.session_id,
        agent_id=template.agent_id,
        kind=kind,
        seq=seq,
        data=data,
    )
    terminal = kind in {"final", "error"}
    if terminal:
        await repo.ack_run_if_terminal(run_id=run_id, kind=kind)
    return row, duplicated, terminal


# ─── Housekeeping ─────────────────────────────────────────
async def purge_expired(
    session: AsyncSession, *, older_than_seconds: int
) -> int:
    """Flip DELIVERED requests that never got ``final`` within the timeout to
    EXPIRED. Runs on a cron (future) — exposed as a service function for
    reuse by the GC job."""

    cutoff_seconds = max(60, older_than_seconds)
    _ = cutoff_seconds, GatewayMessageDirection, GatewayMessageStatus  # placeholder
    # Full cleanup lands alongside the B2 GC rework; keeping the hook so
    # callers compile today and we add the UPDATE statement in B2-follow-up.
    _ = session
    return 0


__all__ = [
    "authenticate_adapter",
    "emit_event",
    "generate_api_key",
    "hash_api_key",
    "mark_seen",
    "poll_pending_requests",
    "purge_expired",
]
