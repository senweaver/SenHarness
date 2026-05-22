"""Agent View runtime API — snapshot + control endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.security import decode_token, utcnow_naive
from app.db.session import get_session_factory
from app.services import agent_runtime as runtime_svc
from app.services import inflight_run as inflight_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Schemas (mirror RuntimeSnapshot dataclasses) ──────────
class _RunCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    stuck_reason: str | None
    orphan: bool
    subagent_count: int


class _SubagentCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    parent_run_id: uuid.UUID
    name: str
    state: str


class AgentRuntimeSnapshot(BaseModel):
    summary: dict[str, int]
    runs: list[_RunCard]
    subagents: list[_SubagentCard]
    timestamp: int


class WorkspaceRuntimeSummary(BaseModel):
    workspace_id: uuid.UUID
    running: int
    stuck: int
    orphan: int
    queued: int


class WorkspaceRuntimeSummariesOut(BaseModel):
    summaries: list[WorkspaceRuntimeSummary]
    timestamp: int


class _StopResult(BaseModel):
    run_id: uuid.UUID
    state: str
    previous_state: str
    killed_at: str
    cancel_dispatched: bool


class _SweepResult(BaseModel):
    stale_seen: int
    reaped: int
    spared_alive: int
    notified_count: int


# ─── Snapshot ──────────────────────────────────────────────
@router.get("/snapshot", response_model=AgentRuntimeSnapshot)
async def get_snapshot(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AgentRuntimeSnapshot:
    """Live workspace-scoped view of all in-flight runs."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    snap = await runtime_svc.build_snapshot(db, workspace_id=ws_id)
    return AgentRuntimeSnapshot(
        summary=snap.summary,
        runs=[_RunCard.model_validate(r) for r in snap.runs],
        subagents=[_SubagentCard.model_validate(s) for s in snap.subagents],
        timestamp=snap.timestamp,
    )


# ─── Cross-workspace summaries (workspace switcher) ────────
@router.get("/summaries", response_model=WorkspaceRuntimeSummariesOut)
async def get_workspace_summaries(
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> WorkspaceRuntimeSummariesOut:
    """Per-workspace runtime counters for every workspace the caller belongs to.

    Intentionally not scoped to a single workspace — the workspace
    switcher renders status dots for every membership in one pass.
    Membership is hard-capped (alphabetical by name) in the service
    layer to keep this O(small).
    """
    summaries = await runtime_svc.build_workspace_summaries(db, identity_id=identity_id)
    return WorkspaceRuntimeSummariesOut(
        summaries=[
            WorkspaceRuntimeSummary(
                workspace_id=s.workspace_id,
                running=s.running,
                stuck=s.stuck,
                orphan=s.orphan,
                queued=s.queued,
            )
            for s in summaries
        ],
        timestamp=int(utcnow_naive().timestamp() * 1000),
    )


# ─── Control endpoints ─────────────────────────────────────
@router.post("/runs/{run_id}/stop", response_model=_StopResult)
async def stop_run(
    run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> _StopResult:
    """Cancel an in-flight run via the kernel cancel hook + audit."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    result = await inflight_svc.force_recycle_run(
        db,
        workspace_id=ws_id,
        run_id=run_id,
        actor_identity_id=identity_id,
    )
    await db.commit()
    return _StopResult(
        run_id=uuid.UUID(result["run_id"]),
        state=result["state"],
        previous_state=result["previous_state"],
        killed_at=result["killed_at"],
        cancel_dispatched=result["cancel_dispatched"],
    )


@router.post("/runs/{run_id}/recycle", response_model=_StopResult)
async def recycle_run(
    run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> _StopResult:
    """Mark the row LOST so the user can /retry from the chat view.

    Placeholder for v1 — actual re-issue of the original RunRequest is
    a follow-up. Today we share the stop path so the UI can offer the
    same "this run is gone" affordance on stalled cards.
    """
    return await stop_run(
        run_id=run_id,
        db=db,
        identity_id=identity_id,
        workspace_id=workspace_id,
    )


@router.post("/sweep", response_model=_SweepResult)
async def sweep_lost_runs(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> _SweepResult:
    """Manually trigger the LOST-run reaper for this workspace."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    result = await inflight_svc.reap_stale(db)
    await db.commit()
    return _SweepResult(
        stale_seen=int(result.get("stale_seen", 0)),
        reaped=int(result.get("reaped", 0)),
        spared_alive=int(result.get("spared_alive", 0)),
        notified_count=int(result.get("notified_count", 0)),
    )


def _parse_subscribe_workspaces(raw: str | None) -> list[uuid.UUID] | None:
    """Parse the optional ``subscribe_workspaces`` query parameter.

    Accepts a comma-separated list of UUIDs. Returns ``None`` when the
    parameter is absent (so the JWT ``ws`` claim is used) and an empty
    list when present-but-malformed (caller maps to a 4400 close).
    """
    if raw is None:
        return None
    out: list[uuid.UUID] = []
    for part in raw.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        try:
            out.append(uuid.UUID(chunk))
        except ValueError:
            return []
    return out


# ─── WebSocket ─────────────────────────────────────────────
@router.websocket("/ws")
async def agent_runtime_ws(websocket: WebSocket) -> None:
    """WS push for the Agent View + workspace switcher.

    Frame types:
    * ``runtime.run_card_update`` — emitted per live run.
    * ``runtime.workspace_summary`` — emitted on lifecycle transitions.

    Subscription model:
    * Without ``subscribe_workspaces`` — single workspace from the JWT
      ``ws`` claim (legacy Agent View behaviour).
    * With ``subscribe_workspaces=<uuid,uuid,...>`` — multi-tenant fan
      out, one socket subscribed to all listed workspaces. Each id
      must belong to the caller; any failure → 4403.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return

    try:
        payload = decode_token(token, expected_kind="access")
        identity_id = uuid.UUID(payload["sub"])
        jwt_workspace_id = uuid.UUID(payload["ws"]) if payload.get("ws") else None
    except Exception:
        await websocket.close(code=4401)
        return

    raw_subscribe = websocket.query_params.get("subscribe_workspaces")
    parsed_subscribe = _parse_subscribe_workspaces(raw_subscribe)
    if raw_subscribe is not None and not parsed_subscribe:
        await websocket.close(code=4400)
        return

    target_workspaces: list[uuid.UUID]
    if parsed_subscribe is not None:
        target_workspaces = parsed_subscribe
    elif jwt_workspace_id is not None:
        target_workspaces = [jwt_workspace_id]
    else:
        await websocket.close(code=4403)
        return

    factory = get_session_factory()
    async with factory() as db:
        try:
            for ws_id in target_workspaces:
                await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
        except Exception:
            await websocket.close(code=4403)
            return

    await websocket.accept()

    queues: list[tuple[uuid.UUID, asyncio.Queue[dict]]] = []
    for ws_id in target_workspaces:
        q = await runtime_svc.RUNTIME_BUS.subscribe(ws_id)
        queues.append((ws_id, q))

    fan_in: asyncio.Queue[dict] = asyncio.Queue(maxsize=runtime_svc.RUNTIME_BUS.QUEUE_MAX)

    async def _forward(source: asyncio.Queue[dict]) -> None:
        while True:
            event = await source.get()
            try:
                fan_in.put_nowait(event)
            except asyncio.QueueFull:
                # Match the bus's drop-on-overflow behaviour so a
                # wedged socket never wedges the publisher.
                log.warning("agent_runtime ws fan-in full; dropping event")

    forwarders = [asyncio.create_task(_forward(q)) for _, q in queues]

    try:
        while True:
            event = await fan_in.get()
            try:
                await websocket.send_text(json.dumps(event))
            except (WebSocketDisconnect, RuntimeError):
                break
    except WebSocketDisconnect:
        pass
    finally:
        for t in forwarders:
            t.cancel()
        for ws_id, q in queues:
            await runtime_svc.RUNTIME_BUS.unsubscribe(ws_id, q)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
