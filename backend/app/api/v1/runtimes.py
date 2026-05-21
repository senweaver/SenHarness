"""Agent Runtime switching + side-by-side comparison.

Two operator-facing endpoints:

``POST /api/v1/agents/{agent_id}/runtime/switch``
    Change ``backend_kind`` / ``backend_adapter_id`` on an existing Agent
    without rebuilding persona / memory / skills / sessions. Mirrors
    ``PATCH /agents/{id}`` but with explicit validation and an audit record
    so runtime moves show up in the activity log regardless of UI path.

``POST /api/v1/agents/{agent_id}/runtime/compare``
    Run the same prompt against multiple runtimes in parallel and return
    ``{latency_ms, tokens, cost, final_text, verdict}`` per candidate so the
    UI renders a side-by-side performance card. Caps the fan-out at 4 so
    one click can't burn a week's budget.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.harness.evaluator import evaluate_run
from app.agents.kernels.base import RunEventKind, RunRequest
from app.agents.kernels.registry import (
    available_kinds,
    describe,
    get_backend,
)
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.agent import is_known_backend_kind
from app.repositories.agent import AgentRepository
from app.repositories.backend_adapter import BackendAdapterRepository
from app.services import audit as audit_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["runtimes"])

MAX_COMPARE_RUNTIMES = 4
MAX_COMPARE_PROMPT_CHARS = 4000


# ─── Schemas ─────────────────────────────────────────────────
class RuntimeSwitchIn(BaseModel):
    backend_kind: str = Field(
        ...,
        description="Target runtime kind (e.g. 'native', 'openclaw'). "
        "Must be a registered kind; unknown kinds are rejected.",
    )
    backend_adapter_id: uuid.UUID | None = Field(
        default=None,
        description="Required when the target runtime needs a remote adapter "
        "(e.g. OpenClaw). Leave null for in-process runtimes.",
    )
    note: str | None = Field(
        default=None,
        max_length=280,
        description="Free-form audit note (surfaced in /audit).",
    )


class RuntimeSwitchOut(BaseModel):
    agent_id: uuid.UUID
    backend_kind: str
    backend_adapter_id: uuid.UUID | None
    switched_from: str


class RuntimeCompareIn(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_COMPARE_PROMPT_CHARS)
    runtimes: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_COMPARE_RUNTIMES,
        description=(
            "List of backend kinds to run in parallel. Each kind must be "
            "registered. Unknown kinds fail the request."
        ),
    )
    include_eval: bool = Field(
        default=True,
        description="Score each candidate with the independent evaluator.",
    )


class RuntimeCompareCandidate(BaseModel):
    runtime: str
    ok: bool
    latency_ms: int
    tokens: dict[str, int]
    cost_usd: float
    final_text: str | None
    error: str | None
    verdict: dict[str, Any] | None


class RuntimeCompareOut(BaseModel):
    agent_id: uuid.UUID
    prompt: str
    candidates: list[RuntimeCompareCandidate]


# ─── Helpers ─────────────────────────────────────────────────
def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _load_agent(
    db, *, agent_id: uuid.UUID, workspace_id: uuid.UUID
):
    repo = AgentRepository(db)
    agent = await repo.get_by(id=agent_id, workspace_id=workspace_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return agent


# ─── Runtime endpoints ───────────────────────────────────────
@router.get(
    "/runtimes",
    summary="List registered Agent runtimes",
    description=(
        "Every backend registered via ``kernels.registry.register`` shows up "
        "here. The UI runtime picker consumes this endpoint so third-party "
        "adapters appear without any frontend code changes."
    ),
)
def list_runtimes() -> list[dict[str, Any]]:
    return describe()


@router.post(
    "/{agent_id}/runtime/switch",
    response_model=RuntimeSwitchOut,
    summary="Switch an Agent's runtime without rebuilding it",
)
async def switch_runtime(
    agent_id: uuid.UUID,
    body: RuntimeSwitchIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> RuntimeSwitchOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)

    if get_backend(body.backend_kind) is None or not is_known_backend_kind(
        body.backend_kind
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "runtime.unknown_kind",
                "message": f"Runtime {body.backend_kind!r} is not registered.",
                "available": sorted(available_kinds()),
            },
        )

    if body.backend_adapter_id is not None:
        adapter = await BackendAdapterRepository(db).get_by(
            id=body.backend_adapter_id, workspace_id=ws_id
        )
        if adapter is None:
            raise HTTPException(status_code=404, detail="adapter_not_found")

    agent = await _load_agent(db, agent_id=agent_id, workspace_id=ws_id)
    previous = agent.backend_kind
    agent.backend_kind = body.backend_kind
    agent.backend_adapter_id = body.backend_adapter_id

    await audit_svc.record(
        db,
        action="agent.runtime_switch",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent.id,
        summary=(
            f"switched agent {agent.name!r} runtime "
            f"{previous!r} → {body.backend_kind!r}"
        ),
        metadata={
            "from": previous,
            "to": body.backend_kind,
            "adapter_id": str(body.backend_adapter_id)
            if body.backend_adapter_id
            else None,
            "note": body.note,
        },
        request=request,
    )
    await db.commit()
    return RuntimeSwitchOut(
        agent_id=agent.id,
        backend_kind=agent.backend_kind,
        backend_adapter_id=agent.backend_adapter_id,
        switched_from=previous,
    )


@router.post(
    "/{agent_id}/runtime/compare",
    response_model=RuntimeCompareOut,
    summary="Run the same prompt across several runtimes (side-by-side)",
)
async def compare_runtimes(
    agent_id: uuid.UUID,
    body: RuntimeCompareIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> RuntimeCompareOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)

    # Dedup + validation
    runtimes = list(dict.fromkeys(body.runtimes))
    for k in runtimes:
        if get_backend(k) is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "runtime.unknown_kind",
                    "message": f"Runtime {k!r} is not registered.",
                    "available": sorted(available_kinds()),
                },
            )

    agent = await _load_agent(db, agent_id=agent_id, workspace_id=ws_id)

    async def _run_one(kind: str) -> RuntimeCompareCandidate:
        backend = get_backend(kind)
        if backend is None:  # defensive — validated above
            return RuntimeCompareCandidate(
                runtime=kind,
                ok=False,
                latency_ms=0,
                tokens={"input": 0, "output": 0},
                cost_usd=0.0,
                final_text=None,
                error="runtime_vanished",
                verdict=None,
            )

        req = RunRequest(
            run_id=uuid.uuid4(),
            workspace_id=ws_id,
            agent_id=agent.id,
            session_id=uuid.uuid4(),  # throwaway, not persisted
            identity_id=identity_id,
            user_text=body.prompt,
            message_history=[],
            attachments=[],
            toolbox=[],
            skills=[],
            policy={
                "persona_md": agent.persona_md,
                "workspace_id": str(ws_id),
                "_ephemeral_compare": True,
            },
            iteration_budget=6,
        )
        started = time.perf_counter()
        final_text = ""
        cost = 0.0
        tokens = {"input": 0, "output": 0}
        err: str | None = None

        try:
            # Hard timeout so one runaway runtime can't stall the whole panel.
            async def _consume() -> None:
                nonlocal final_text, cost, tokens
                async for ev in backend.run(req):
                    if ev.kind == RunEventKind.DELTA:
                        final_text += str(ev.data.get("text") or "")
                    elif ev.kind == RunEventKind.USAGE:
                        tokens = ev.data.get("tokens") or tokens
                        cost = float(ev.data.get("cost") or 0.0)
                    elif ev.kind == RunEventKind.ERROR:
                        raise RuntimeError(
                            str(ev.data.get("message") or ev.data.get("code"))
                        )

            await asyncio.wait_for(_consume(), timeout=45.0)
        except TimeoutError:
            err = "timeout"
        except Exception as e:  # pragma: no cover
            err = str(e)
            log.info("runtime compare %s errored: %s", kind, e)

        latency_ms = int((time.perf_counter() - started) * 1000)
        verdict_dict: dict[str, Any] | None = None
        if body.include_eval and final_text and not err:
            try:
                verdict = await evaluate_run(
                    user_text=body.prompt, final_text=final_text
                )
                verdict_dict = verdict.to_dict()
            except Exception:  # pragma: no cover
                pass

        return RuntimeCompareCandidate(
            runtime=kind,
            ok=err is None and bool(final_text),
            latency_ms=latency_ms,
            tokens=tokens,
            cost_usd=round(cost, 6),
            final_text=final_text or None,
            error=err,
            verdict=verdict_dict,
        )

    candidates = await asyncio.gather(
        *[_run_one(k) for k in runtimes], return_exceptions=False
    )
    return RuntimeCompareOut(
        agent_id=agent.id,
        prompt=body.prompt,
        candidates=list(candidates),
    )
