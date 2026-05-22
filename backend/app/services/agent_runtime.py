"""Agent View runtime — workspace-wide snapshot + pub/sub for the live
cards page.

The Agent View page (``/agent-view``) renders one card per live inflight
run in the active workspace. Two surfaces feed it:

* :func:`build_snapshot` — pull every active :class:`InflightRun` for
  the workspace, derive ``stuck_reason`` / ``orphan`` / age fields, and
  return the JSON-shaped payload the page hydrates on mount.
* :class:`RuntimeEventBus` — an in-memory pub/sub the native runner
  publishes ``runtime.run_card_update`` events to; the agent-runtime
  WebSocket endpoint forwards them to subscribed tabs.

The bus mirrors :class:`ApprovalManager`'s asyncio.Queue pattern.
Redis would let multiple workers share the topic, but a single-pod v1
keeps the surface honest. The Redis port is left as a TODO so it doesn't
get prematurely abstracted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.session import get_session_factory
from app.repositories.inflight_run import InflightRunRepository

log = logging.getLogger(__name__)


StuckReason = Literal["idle_silent", "tool_silent", "hard_cap"]


# ─── Heuristics ─────────────────────────────────────────────
# Aligns with the plan §C1 cut-offs. ``ms`` units throughout so the
# frontend doesn't have to flip seconds/milliseconds.
IDLE_SILENT_MS = 30_000  # no first delta after 30s
TOOL_SILENT_MS = 60_000  # tool running but silent for 60s
HARD_CAP_MS = 600_000  # 10 minutes wall-clock


@dataclass(slots=True, frozen=True)
class RuntimeRunCard:
    """JSON-friendly card payload — mirrors the frontend ``RuntimeRunCard`` shape."""

    session_id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str | None
    agent_avatar_url: str | None
    user_name: str | None
    run_id: uuid.UUID
    state: str
    current_phase: str | None
    running_tool_name: str | None
    first_token_received: bool
    queue_len: int
    age_ms: int
    ms_since_last_event: int
    stuck_reason: StuckReason | None
    orphan: bool
    subagent_count: int


@dataclass(slots=True, frozen=True)
class RuntimeSubagentCard:
    """Lightweight subagent rollup — currently only count."""

    parent_run_id: uuid.UUID
    name: str
    state: str


@dataclass(slots=True, frozen=True)
class RuntimeSnapshot:
    summary: dict[str, int]
    runs: list[RuntimeRunCard]
    subagents: list[RuntimeSubagentCard]
    timestamp: int


@dataclass(slots=True, frozen=True)
class WorkspaceRuntimeSummary:
    """Per-workspace runtime counters for the workspace switcher row."""

    workspace_id: uuid.UUID
    running: int
    stuck: int
    orphan: int
    queued: int


# ─── Pub/sub bus ────────────────────────────────────────────
class RuntimeEventBus:
    """Workspace-scoped event fan-out for Agent View tabs.

    Each subscriber gets its own ``asyncio.Queue`` so a slow consumer
    only stalls itself, never the publisher. The bus drops events when
    a queue is full (max 256 pending updates per tab) so a wedged
    browser tab doesn't pin memory.

    TODO(C-followup): replace with Redis pub/sub when multi-pod
    deployments are supported. The publisher and subscriber interfaces
    are deliberately narrow to ease that swap.
    """

    QUEUE_MAX = 256

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subs: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}

    async def subscribe(self, workspace_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.QUEUE_MAX)
        async with self._lock:
            self._subs.setdefault(workspace_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self,
        workspace_id: uuid.UUID,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with self._lock:
            subs = self._subs.get(workspace_id)
            if subs is None:
                return
            subs.discard(queue)
            if not subs:
                del self._subs[workspace_id]

    def subscriber_count(self, workspace_id: uuid.UUID) -> int:
        return len(self._subs.get(workspace_id, ()))

    def publish_nowait(self, workspace_id: uuid.UUID, payload: dict[str, Any]) -> None:
        """Fan an event out without blocking.

        Called from the native runner (which is already inside an
        event loop). We never await here — a backed-up subscriber gets
        its queue dropped instead of pausing the runner.
        """
        subs = self._subs.get(workspace_id)
        if not subs:
            return
        for queue in list(subs):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning(
                    "agent_runtime queue full; dropping update workspace=%s",
                    workspace_id,
                )


RUNTIME_BUS = RuntimeEventBus()


# ─── Snapshot helpers ───────────────────────────────────────
def _derive_stuck_reason(
    *,
    age_ms: int,
    ms_since_last_event: int,
    running_tool_name: str | None,
    first_token_received: bool,
) -> StuckReason | None:
    if age_ms > HARD_CAP_MS:
        return "hard_cap"
    if running_tool_name and ms_since_last_event > TOOL_SILENT_MS:
        return "tool_silent"
    if not first_token_received and ms_since_last_event > IDLE_SILENT_MS:
        return "idle_silent"
    return None


def _row_to_card(
    row: InflightRun,
    *,
    now: datetime,
    agent_name: str | None,
    agent_avatar_url: str | None,
    user_name: str | None,
    subagent_count: int,
    orphan: bool,
) -> RuntimeRunCard:
    age_ms = int((now - row.started_at).total_seconds() * 1000)
    ms_since_last_event = int((now - row.last_seen_at).total_seconds() * 1000)
    snapshot = row.request_snapshot or {}
    first_token_received = bool(snapshot.get("first_token_received")) or row.last_event_seq > 0
    queue_len = int(snapshot.get("queue_len") or 0)
    return RuntimeRunCard(
        session_id=row.session_id,
        agent_id=row.agent_id,
        agent_name=agent_name,
        agent_avatar_url=agent_avatar_url,
        user_name=user_name,
        run_id=row.run_id,
        state=row.state.value,
        current_phase=row.current_phase,
        running_tool_name=row.running_tool_name,
        first_token_received=first_token_received,
        queue_len=queue_len,
        age_ms=max(0, age_ms),
        ms_since_last_event=max(0, ms_since_last_event),
        stuck_reason=_derive_stuck_reason(
            age_ms=age_ms,
            ms_since_last_event=ms_since_last_event,
            running_tool_name=row.running_tool_name,
            first_token_received=first_token_received,
        ),
        orphan=orphan,
        subagent_count=subagent_count,
    )


async def build_snapshot(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> RuntimeSnapshot:
    """Pull all active inflight runs for ``workspace_id``."""
    from sqlalchemy import select

    from app.db.models.agent import Agent
    from app.db.models.identity import Identity

    repo = InflightRunRepository(db)
    rows = list(await repo.list_active_for_workspace(workspace_id=workspace_id, limit=200))
    now = utcnow_naive()

    # Join: agent name + avatar + originating identity email.
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    identity_ids = {r.identity_id for r in rows if r.identity_id}
    agent_lookup: dict[uuid.UUID, Agent] = {}
    identity_lookup: dict[uuid.UUID, Identity] = {}
    if agent_ids:
        result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
        for a in result.scalars():
            agent_lookup[a.id] = a
    if identity_ids:
        result = await db.execute(select(Identity).where(Identity.id.in_(identity_ids)))
        for ident in result.scalars():
            identity_lookup[ident.id] = ident

    subscriber_count = RUNTIME_BUS.subscriber_count(workspace_id)
    cards: list[RuntimeRunCard] = []
    summary = {
        "running": 0,
        "stuck": 0,
        "orphan": 0,
        "queued": 0,
        "subagents_active": 0,
    }
    for row in rows:
        agent = agent_lookup.get(row.agent_id) if row.agent_id else None
        identity = identity_lookup.get(row.identity_id) if row.identity_id else None
        card = _row_to_card(
            row,
            now=now,
            agent_name=agent.name if agent else None,
            agent_avatar_url=agent.avatar_url if agent else None,
            user_name=identity.email if identity else None,
            subagent_count=0,
            orphan=subscriber_count == 0,
        )
        cards.append(card)
        if card.state == InflightRunState.RUNNING.value:
            summary["running"] += 1
        if card.stuck_reason is not None:
            summary["stuck"] += 1
        if card.orphan:
            summary["orphan"] += 1
        summary["queued"] += card.queue_len

    return RuntimeSnapshot(
        summary=summary,
        runs=cards,
        subagents=[],
        timestamp=int(now.timestamp() * 1000),
    )


# ─── Runner-side helpers ────────────────────────────────────
async def write_phase(
    *,
    run_id: uuid.UUID,
    phase: str | None,
    running_tool_name: str | None = "__keep__",
) -> None:
    """Update the live phase/tool columns. Best-effort.

    ``running_tool_name`` default ``"__keep__"`` means "don't touch";
    pass ``None`` to clear, or a string to set.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            repo = InflightRunRepository(db)
            row = await repo.get_by_run_id(run_id=run_id)
            if row is None:
                return
            row.current_phase = phase
            if running_tool_name != "__keep__":
                row.running_tool_name = running_tool_name  # type: ignore[assignment]
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("agent_runtime.write_phase failed run=%s", run_id)


def publish_run_card_update(
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """Fan a ``runtime.run_card_update`` event out to subscribed tabs."""
    RUNTIME_BUS.publish_nowait(
        workspace_id,
        {
            "type": "runtime.run_card_update",
            "data": {
                "run_id": str(run_id),
                "session_id": str(session_id),
                **payload,
            },
        },
    )


def publish_workspace_summary_delta(
    *,
    workspace_id: uuid.UUID,
    summary: WorkspaceRuntimeSummary,
) -> None:
    """Fan a ``runtime.workspace_summary`` event to subscribed tabs.

    Emitted on inflight lifecycle transitions only (register / terminal
    transition / sweep) — token-delta hot-path callers must not invoke
    this, since the counts they would publish are unchanged.
    """
    RUNTIME_BUS.publish_nowait(
        workspace_id,
        {
            "type": "runtime.workspace_summary",
            "data": {
                "workspace_id": str(summary.workspace_id),
                "running": summary.running,
                "stuck": summary.stuck,
                "orphan": summary.orphan,
                "queued": summary.queued,
            },
        },
    )


async def build_workspace_summaries(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    workspace_limit: int = 50,
) -> list[WorkspaceRuntimeSummary]:
    """Per-workspace running/stuck/orphan/queued counts for one identity.

    Resolves the caller's active workspace memberships (hard-capped to
    ``workspace_limit`` alphabetically), pulls every active inflight
    row across those workspaces in a single query, and reuses the same
    ``_derive_stuck_reason`` / ``subscriber_count == 0`` logic that
    :func:`build_snapshot` applies per row. Workspaces with zero
    active runs are still returned so the UI can render an "idle"
    state for them.
    """
    from app.services import workspace as ws_svc

    workspace_ids = await ws_svc.list_active_workspace_ids_for_identity(
        db, identity_id=identity_id, limit=workspace_limit
    )
    if not workspace_ids:
        return []

    repo = InflightRunRepository(db)
    rows = await repo.list_active_for_workspaces(workspace_ids=workspace_ids)
    now = utcnow_naive()

    counters: dict[uuid.UUID, dict[str, int]] = {
        ws_id: {"running": 0, "stuck": 0, "orphan": 0, "queued": 0} for ws_id in workspace_ids
    }

    for row in rows:
        bucket = counters.get(row.workspace_id)
        if bucket is None:
            continue
        age_ms = int((now - row.started_at).total_seconds() * 1000)
        ms_since_last_event = int((now - row.last_seen_at).total_seconds() * 1000)
        snapshot = row.request_snapshot or {}
        first_token_received = bool(snapshot.get("first_token_received")) or row.last_event_seq > 0
        queue_len = int(snapshot.get("queue_len") or 0)
        stuck_reason = _derive_stuck_reason(
            age_ms=max(0, age_ms),
            ms_since_last_event=max(0, ms_since_last_event),
            running_tool_name=row.running_tool_name,
            first_token_received=first_token_received,
        )
        orphan = RUNTIME_BUS.subscriber_count(row.workspace_id) == 0
        if row.state == InflightRunState.RUNNING:
            bucket["running"] += 1
        if stuck_reason is not None:
            bucket["stuck"] += 1
        if orphan:
            bucket["orphan"] += 1
        bucket["queued"] += queue_len

    return [
        WorkspaceRuntimeSummary(
            workspace_id=ws_id,
            running=counters[ws_id]["running"],
            stuck=counters[ws_id]["stuck"],
            orphan=counters[ws_id]["orphan"],
            queued=counters[ws_id]["queued"],
        )
        for ws_id in workspace_ids
    ]


async def publish_workspace_summary_for(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> None:
    """Recompute and fan a fresh ``runtime.workspace_summary`` out.

    Convenience for lifecycle hooks: builds the summary for the
    workspace and emits it. Best-effort — never raises into the caller.
    """
    try:
        summary = await build_workspace_summary(db, workspace_id=workspace_id)
        publish_workspace_summary_delta(workspace_id=workspace_id, summary=summary)
    except Exception:  # pragma: no cover - best-effort
        log.exception(
            "agent_runtime publish_workspace_summary_for failed workspace=%s",
            workspace_id,
        )


async def build_workspace_summary(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> WorkspaceRuntimeSummary:
    """Recompute one workspace's runtime summary.

    Used after a lifecycle transition so the workspace-summary publish
    helper carries fresh counts. Reuses the same stuck/orphan
    derivation as :func:`build_workspace_summaries`.
    """
    repo = InflightRunRepository(db)
    rows = await repo.list_active_for_workspaces(workspace_ids=[workspace_id])
    now = utcnow_naive()
    counters = {"running": 0, "stuck": 0, "orphan": 0, "queued": 0}
    subscribers = RUNTIME_BUS.subscriber_count(workspace_id)

    for row in rows:
        age_ms = int((now - row.started_at).total_seconds() * 1000)
        ms_since_last_event = int((now - row.last_seen_at).total_seconds() * 1000)
        snapshot = row.request_snapshot or {}
        first_token_received = bool(snapshot.get("first_token_received")) or row.last_event_seq > 0
        queue_len = int(snapshot.get("queue_len") or 0)
        stuck_reason = _derive_stuck_reason(
            age_ms=max(0, age_ms),
            ms_since_last_event=max(0, ms_since_last_event),
            running_tool_name=row.running_tool_name,
            first_token_received=first_token_received,
        )
        if row.state == InflightRunState.RUNNING:
            counters["running"] += 1
        if stuck_reason is not None:
            counters["stuck"] += 1
        if subscribers == 0:
            counters["orphan"] += 1
        counters["queued"] += queue_len

    return WorkspaceRuntimeSummary(
        workspace_id=workspace_id,
        running=counters["running"],
        stuck=counters["stuck"],
        orphan=counters["orphan"],
        queued=counters["queued"],
    )


__all__ = [
    "HARD_CAP_MS",
    "IDLE_SILENT_MS",
    "RUNTIME_BUS",
    "TOOL_SILENT_MS",
    "RuntimeEventBus",
    "RuntimeRunCard",
    "RuntimeSnapshot",
    "RuntimeSubagentCard",
    "StuckReason",
    "WorkspaceRuntimeSummary",
    "build_snapshot",
    "build_workspace_summaries",
    "build_workspace_summary",
    "publish_run_card_update",
    "publish_workspace_summary_delta",
    "publish_workspace_summary_for",
    "write_phase",
]
