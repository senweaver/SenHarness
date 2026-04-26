"""Approval service — persistence + runtime integration.

Exposes:
- ``make_approval_callback(...)``: returns an async ``(tool_name, args) -> bool``
  suitable for ``pydantic_ai_shields.ToolGuard(approval_callback=...)``.
  Each call creates a DB row, registers a pending future on the
  ``ApprovalManager``, waits for the decision (with timeout), updates the row,
  and returns the boolean outcome.

- ``decide(...)``: used by the WS handler / REST endpoint to resolve a pending
  approval. Updates DB + signals the in-memory future.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from app.agents.harness.approvals import APPROVAL_MANAGER, ApprovalCallback
from app.core.security import utcnow_naive
from app.db.models.approval import ApprovalStatus
from app.db.session import get_session_factory
from app.repositories.approval import ApprovalRepository

log = logging.getLogger(__name__)


def make_approval_callback(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    requested_by_identity_id: uuid.UUID | None,
    ttl_seconds: int = 300,
    extra: dict[str, Any] | None = None,
) -> ApprovalCallback:
    """Build the callback injected into ``ToolGuard``.

    The callback is stateless — it uses the module-level ``APPROVAL_MANAGER``
    and opens a fresh DB session per request so it doesn't leak the runner's
    DB session across approval waits (which could be minutes long).
    """

    async def callback(tool_name: str, args: dict[str, Any]) -> bool:
        approval_id = uuid.uuid4()
        # 1) Persist pending row.
        try:
            async with get_session_factory()() as db:
                repo = ApprovalRepository(db)
                row = await repo.create(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    tool_args=_safe_jsonify(args),
                    summary=_compose_summary(tool_name, args),
                    requested_by_identity_id=requested_by_identity_id,
                    expires_at=utcnow_naive() + timedelta(seconds=ttl_seconds),
                )
                approval_id = row.id
                await db.commit()
        except Exception:
            log.exception("failed to persist approval row; using in-memory only")

        # 2) Register in-memory pending future.
        await APPROVAL_MANAGER.register(
            approval_id=approval_id,
            session_id=session_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            tool_args=_safe_jsonify(args),
            summary=_compose_summary(tool_name, args),
            ttl=timedelta(seconds=ttl_seconds),
            extra=extra or {},
        )

        # 3) Wait.
        approved, timed_out = await APPROVAL_MANAGER.wait(
            approval_id, timeout_s=ttl_seconds + 5
        )

        # 4) Persist decision (if not already written by the decide endpoint).
        #    Timeout path writes status=EXPIRED explicitly so the audit feed
        #    distinguishes stale requests from user-denied ones.
        try:
            async with get_session_factory()() as db:
                repo = ApprovalRepository(db)
                row = await repo.decide(
                    approval_id=approval_id,
                    workspace_id=workspace_id,
                    approved=approved,
                    reason="timeout" if timed_out else None,
                    decided_by_identity_id=None,
                    now=utcnow_naive(),
                    status_override=(
                        ApprovalStatus.EXPIRED if timed_out else None
                    ),
                )
                if row is not None and row.status == ApprovalStatus.PENDING:
                    # Decide moved it to approved/denied — if it's still pending
                    # here that means the row was already decided by someone
                    # else (WS/REST), which is the happy path.
                    pass
                await db.commit()
        except Exception:
            log.exception("failed to persist approval decision")

        log.info(
            "approval %s %s (tool=%s approved=%s timed_out=%s)",
            approval_id,
            "expired" if timed_out else ("approved" if approved else "denied"),
            tool_name,
            approved,
            timed_out,
        )
        return approved

    return callback


def _safe_jsonify(args: dict[str, Any]) -> dict[str, Any]:
    """Keep approval payloads compact + JSON-safe; stringify oddities."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) else v[:2000]
        elif isinstance(v, (list, dict)):
            try:
                import json

                out[k] = json.loads(json.dumps(v, default=str))
            except Exception:
                out[k] = str(v)[:2000]
        else:
            out[k] = str(v)[:2000]
    return out


def _compose_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Short human-readable line we show in the approval card title."""
    if tool_name == "execute":
        cmd = str(args.get("command", ""))[:120]
        return f"$ {cmd}"
    if tool_name in ("write_file", "edit_file"):
        path = str(args.get("path", args.get("file_path", "?")))
        return f"{tool_name} → {path}"
    if tool_name == "delete_file":
        return f"delete {args.get('path') or args.get('file_path')}"
    kv = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:4])
    return f"{tool_name}({kv})"

