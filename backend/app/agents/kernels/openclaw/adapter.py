"""OpenClawBackend — dispatch a RunRequest to a remote worker via the gateway.

High-level flow:

1. Resolve the ``backend_adapter_id`` from ``RunRequest.policy`` → if missing
   or the adapter row has ``enabled=False`` we emit a single ERROR event and
   bail (prevents the WebSocket from sitting idle).
2. Serialize the ``RunRequest`` into JSON (only cheap, worker-relevant fields
   pass through; ``attachments`` bytes are base64 so they survive the wire)
   and insert a ``direction=request`` row.
3. Poll the ``gateway_messages`` table every ``OPENCLAW_RUN_POLL_INTERVAL_MS``
   for new ``direction=event`` rows with ``seq`` greater than the last we've
   forwarded. Each one is lifted to a ``RunEvent`` and yielded upstream.
4. Stop once we see ``final`` or ``error``; stop with a synthetic TIMEOUT error
   after ``OPENCLAW_GATEWAY_RUN_TIMEOUT_S`` seconds with no conclusion.

Cancellation is handled via ``OpenClawBackend.cancel(run_id)``: it appends a
special ``direction=request, kind="cancel"`` row so the next worker poll will
get the cancel instruction. Local state (the in-flight ``run()`` generator)
then gets torn down by the caller (WebSocket layer) cancelling the task.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agents.kernels.base import (
    AgentBackend,
    BackendCapabilities,
    RunEvent,
    RunEventKind,
    RunRequest,
)
from app.core.config import settings
from app.db.models.backend_adapter import BackendAdapter
from app.db.models.gateway_message import GatewayMessageStatus
from app.db.session import get_session_factory
from app.repositories.backend_adapter import BackendAdapterRepository
from app.repositories.gateway import GatewayRepository

log = logging.getLogger(__name__)


# Whitelist of policy fields we ship to the remote worker. Everything else
# (raw persona, shields, budget snapshots, etc.) stays on SenHarness side so
# the remote can't poke at privileged state.
_POLICY_WHITELIST = {
    "autonomy_level",
    "persona_md",
    "context",
    "workspace_id",
    "session_id",
    "skills",
    "todos",
    "sandbox",
    "approvals",
}


class OpenClawBackend(AgentBackend):
    backend_kind = "openclaw"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_streaming=True,
            supports_parallel_tools=False,
            supports_thinking=False,
            supports_native_mcp=False,
            supports_vision=False,
            notes="Remote OpenClaw-compatible worker (via gateway).",
            display_name="OpenClaw (remote worker)",
            description=(
                "Dispatch agent runs to a remote worker reachable only via "
                "the OpenClaw gateway. Use when the worker must run on "
                "user hardware (laptop / on-prem / edge) or talk to a "
                "private LLM endpoint SenHarness can't reach directly."
            ),
            docs_url="/docs/adapters.md",
            requires_adapter=True,
        )

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        adapter_raw = (req.policy or {}).get("backend_adapter_id")
        adapter_id = _parse_uuid(adapter_raw)
        if adapter_id is None:
            yield RunEvent(
                RunEventKind.ERROR,
                {
                    "code": "openclaw.no_adapter",
                    "message": "Agent is configured for OpenClaw backend but no "
                    "backend_adapter_id is bound. Attach a runtime adapter "
                    "under Workspace → Runtimes first.",
                    "retryable": False,
                },
            )
            return

        factory = get_session_factory()
        adapter: BackendAdapter | None
        async with factory() as db:
            adapter = await BackendAdapterRepository(db).get(adapter_id)
            if adapter is None or adapter.workspace_id != req.workspace_id:
                yield RunEvent(
                    RunEventKind.ERROR,
                    {
                        "code": "openclaw.adapter_not_found",
                        "message": "Referenced OpenClaw adapter no longer exists.",
                        "retryable": False,
                    },
                )
                return
            if not adapter.enabled:
                yield RunEvent(
                    RunEventKind.ERROR,
                    {
                        "code": "openclaw.adapter_disabled",
                        "message": f"Adapter {adapter.name!r} is disabled.",
                        "retryable": True,
                    },
                )
                return

            payload = _serialize_request(req)
            await GatewayRepository(db).enqueue_request(
                workspace_id=req.workspace_id,
                adapter_id=adapter.id,
                run_id=req.run_id,
                session_id=req.session_id,
                agent_id=req.agent_id,
                payload=payload,
            )
            await db.commit()

        # Now drain events. Each iteration opens a short-lived DB session so
        # we don't hold connections across the full run lifetime.
        after_seq = -1
        poll_interval = max(
            0.05, settings.OPENCLAW_RUN_POLL_INTERVAL_MS / 1000.0
        )
        deadline = asyncio.get_event_loop().time() + float(
            settings.OPENCLAW_GATEWAY_RUN_TIMEOUT_S
        )

        try:
            while True:
                if asyncio.get_event_loop().time() >= deadline:
                    yield RunEvent(
                        RunEventKind.ERROR,
                        {
                            "code": "openclaw.timeout",
                            "message": "Remote OpenClaw worker did not respond "
                            f"within {settings.OPENCLAW_GATEWAY_RUN_TIMEOUT_S}s.",
                            "retryable": True,
                        },
                    )
                    await self._mark_timeout(req.run_id)
                    return

                async with factory() as db:
                    rows = await GatewayRepository(db).list_events_since(
                        run_id=req.run_id, after_seq=after_seq
                    )

                terminal = False
                for row in rows:
                    after_seq = max(after_seq, row.seq)
                    ev = _row_to_event(row.kind, row.payload_json or {})
                    if ev is None:
                        continue
                    yield ev
                    if ev.kind in (RunEventKind.FINAL, RunEventKind.ERROR):
                        terminal = True
                if terminal:
                    return

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            # The upstream task was cancelled (user hit Stop). Push a cancel
            # request row so the remote can abort cleanly.
            log.info("openclaw run cancelled: run_id=%s", req.run_id)
            await self.cancel(req.run_id)
            raise

    async def cancel(self, run_id: uuid.UUID) -> None:
        factory = get_session_factory()
        async with factory() as db:
            repo = GatewayRepository(db)
            # Find the originating request row to learn workspace_id + adapter_id.
            existing = await repo.list_for_run(run_id=run_id)
            req_rows = [
                r for r in existing if r.direction.value == "request" and r.kind == "run"
            ]
            if not req_rows:
                return
            origin = req_rows[0]
            await repo.enqueue_cancel_event(
                workspace_id=origin.workspace_id,
                adapter_id=origin.adapter_id,
                run_id=run_id,
            )
            await repo.cancel_pending_for_run(run_id=run_id)
            await db.commit()

    async def _mark_timeout(self, run_id: uuid.UUID) -> None:
        factory = get_session_factory()
        async with factory() as db:
            await GatewayRepository(db).mark_run_terminal(
                run_id=run_id, status=GatewayMessageStatus.EXPIRED
            )
            await db.commit()


# ─── Serialization helpers ────────────────────────────────
def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _serialize_request(req: RunRequest) -> dict[str, Any]:
    """Turn a ``RunRequest`` into a plain JSON-safe dict for the worker."""

    policy = {k: v for k, v in (req.policy or {}).items() if k in _POLICY_WHITELIST}

    atts: list[dict[str, Any]] = []
    for att in req.attachments or []:
        entry: dict[str, Any] = {
            "kind": att.get("kind"),
            "mime_type": att.get("mime_type"),
        }
        data = att.get("data")
        if isinstance(data, bytes | bytearray):
            entry["data_b64"] = base64.b64encode(bytes(data)).decode("ascii")
            entry["size_bytes"] = len(data)
        else:
            # Non-binary payloads (already-serialized refs) pass through.
            entry.update({k: v for k, v in att.items() if k not in {"data"}})
        atts.append(entry)

    return {
        "run_id": str(req.run_id),
        "workspace_id": str(req.workspace_id),
        "agent_id": str(req.agent_id),
        "session_id": str(req.session_id),
        "identity_id": str(req.identity_id),
        "user_text": req.user_text,
        "message_history": req.message_history or [],
        "attachments": atts,
        "toolbox": req.toolbox or [],
        "skills": req.skills or [],
        "policy": policy,
        "iteration_budget": int(req.iteration_budget),
        "model_override": req.model_override,
    }


def _row_to_event(kind: str, data: dict[str, Any]) -> RunEvent | None:
    """Map a stored event kind string back onto our ``RunEventKind`` enum."""

    try:
        enum_kind = RunEventKind(kind)
    except ValueError:
        # Unknown kinds are silently dropped instead of surfacing as noise.
        return None
    return RunEvent(enum_kind, data)
