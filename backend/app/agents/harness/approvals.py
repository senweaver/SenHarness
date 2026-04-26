"""Approval runtime — bridges pydantic-ai-shields ``ToolGuard`` with the
WebSocket session so a human can approve/deny sensitive tool calls.

Design
------
- ``ApprovalManager`` is a module-level singleton. It keeps an ``asyncio.Future``
  per pending approval row so any party (WS handler, REST endpoint, auto-expire
  sweeper) can resolve the same wait point.
- The runner (pydantic-ai kernel) creates a ``ToolGuard(require_approval=[...],
  approval_callback=callback)`` where ``callback(tool_name, args)``:
    1. Inserts an ``approvals`` DB row (status=pending).
    2. Emits an ``approval_request`` event via a queue that the WS handler
       drains and pushes to the client.
    3. Awaits the future via ``ApprovalManager.wait(approval_id)``.
    4. Returns True/False to ToolGuard (False raises ``ToolBlocked``).
- Decisions come in from:
    - The WS client via an ``approval_decision`` frame → WS handler calls
      ``ApprovalManager.decide(...)`` which updates the DB and resolves the
      future.
    - A REST endpoint ``POST /api/v1/approvals/{id}/decision`` for out-of-band
      approvals (e.g. Slack button, email link, admin console).

Timeouts
--------
Each approval has a default 5-minute wall-clock TTL. If no decision arrives in
time the future resolves False and the row moves to ``expired``. Configurable
per call via ``timeout_s``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.security import utcnow_naive

log = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    """An outstanding approval: DB id + future the runner is sleeping on."""

    id: uuid.UUID
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    tool_name: str
    tool_args: dict[str, Any]
    summary: str | None
    future: asyncio.Future[bool]
    created_at: datetime
    expires_at: datetime
    # Optional freeform metadata (e.g. the agent name, agent avatar) that the
    # WS handler can use to render the approval card.
    extra: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────────
class ApprovalManager:
    """Thread/event-loop safe registry of pending approvals."""

    def __init__(self) -> None:
        self._pending: dict[uuid.UUID, PendingApproval] = {}
        self._lock = asyncio.Lock()
        # Per-session queues so WS handlers can wake up on "new approval".
        self._session_queues: dict[uuid.UUID, asyncio.Queue[PendingApproval]] = {}

    async def register(
        self,
        *,
        approval_id: uuid.UUID,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        tool_name: str,
        tool_args: dict[str, Any],
        summary: str | None,
        ttl: timedelta = timedelta(minutes=5),
        extra: dict[str, Any] | None = None,
    ) -> PendingApproval:
        now = utcnow_naive()
        fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        entry = PendingApproval(
            id=approval_id,
            session_id=session_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            tool_args=tool_args,
            summary=summary,
            future=fut,
            created_at=now,
            expires_at=now + ttl,
            extra=extra or {},
        )
        async with self._lock:
            self._pending[approval_id] = entry
            q = self._session_queues.get(session_id)
        if q is not None:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:  # pragma: no cover
                log.warning("approval queue full for session %s", session_id)
        return entry

    async def wait(
        self, approval_id: uuid.UUID, *, timeout_s: float
    ) -> tuple[bool, bool]:
        """Block until a decision lands (or the TTL elapses).

        Returns ``(approved, timed_out)``. When ``timed_out`` is True the caller
        should persist the approval row as ``EXPIRED`` instead of ``DENIED``.
        """
        entry = self._pending.get(approval_id)
        if entry is None:
            return False, False
        try:
            approved = await asyncio.wait_for(entry.future, timeout=timeout_s)
            return approved, False
        except TimeoutError:
            await self.decide(approval_id, approved=False, reason="timeout")
            return False, True
        finally:
            async with self._lock:
                self._pending.pop(approval_id, None)

    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        approved: bool,
        reason: str | None = None,
        decided_by: uuid.UUID | None = None,
    ) -> PendingApproval | None:
        async with self._lock:
            entry = self._pending.get(approval_id)
        if entry is None:
            return None
        if not entry.future.done():
            entry.future.set_result(approved)
        entry.extra["decided_reason"] = reason
        entry.extra["decided_by_identity_id"] = (
            str(decided_by) if decided_by is not None else None
        )
        return entry

    async def subscribe_session(self, session_id: uuid.UUID) -> asyncio.Queue[PendingApproval]:
        """Used by the WS handler to get a queue of new approval requests for
        the session it's serving.
        """
        async with self._lock:
            q = self._session_queues.get(session_id)
            if q is None:
                q = asyncio.Queue(maxsize=64)
                self._session_queues[session_id] = q
        return q

    async def unsubscribe_session(self, session_id: uuid.UUID) -> None:
        async with self._lock:
            self._session_queues.pop(session_id, None)

    def peek_pending(self) -> list[PendingApproval]:
        return list(self._pending.values())


APPROVAL_MANAGER = ApprovalManager()


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
DEFAULT_APPROVAL_TOOLS: list[str] = [
    # ConsoleCapability / DockerSandbox tools that can touch the real world.
    "execute",
    "write_file",
    "edit_file",
    "delete_file",
]


def resolve_require_approval(policy: dict[str, Any] | None) -> list[str]:
    """Read ``metadata.sandbox.require_approval`` / ``metadata.approvals`` etc.

    Resolution order:

    1. explicit ``approvals: list``  → use as-is
    2. explicit ``approvals: false / "off"``  → disabled (even on L3)
    3. explicit ``approvals: true``  → DEFAULT_APPROVAL_TOOLS
    4. ``autonomy_level == "l3"`` with no explicit ``approvals`` key  →
       DEFAULT_APPROVAL_TOOLS (L3 auto-enable)
    5. inherit from ``sandbox.require_approval``
    6. ``[]`` (no gate)
    """
    if not policy:
        return []
    sandbox = policy.get("sandbox")
    approvals = policy.get("approvals")
    has_explicit = "approvals" in policy

    # explicit approvals list wins
    if isinstance(approvals, list):
        return [str(x) for x in approvals]
    if approvals is False or approvals == "off":
        return []
    if approvals is True:
        return list(DEFAULT_APPROVAL_TOOLS)

    # L3 auto-enable: high-risk autonomy requires approval unless the user
    # explicitly opted out. We only trigger this when approvals was not set.
    if not has_explicit:
        autonomy = str(policy.get("autonomy_level") or "").lower()
        if autonomy == "l3":
            return list(DEFAULT_APPROVAL_TOOLS)

    # else: inherit from sandbox.require_approval
    if isinstance(sandbox, dict):
        req = sandbox.get("require_approval")
        if isinstance(req, list):
            return [str(x) for x in req]
        if req is True:
            return list(DEFAULT_APPROVAL_TOOLS)

    return []


# Type alias matching pydantic-ai-shields' ApprovalCallback shape.
ApprovalCallback = Callable[[str, dict[str, Any]], Awaitable[bool]]
