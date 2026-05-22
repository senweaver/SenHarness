"""Sub-agent delegation harness.

Wraps the upstream sub-agent capability as a pydantic-ai
``Capability`` and lets the main agent spawn focused workers for
multi-step or parallelizable tasks.

Opt-in per Agent via ``metadata_json.subagents``:

  ```json
  {
    "subagents": {
      "enabled": true,
      "max_nesting_depth": 1,
      "include_general_purpose": true,
      "specs": [
        { "name": "researcher",
          "description": "Dedicated to web research and URL summarization",
          "tools": ["web_search", "web_fetch"] },
        { "name": "writer",
          "description": "Drafts long-form content from collected facts" }
      ]
    }
  }
  ```

Anything omitted falls back to sensible defaults. Passing just
``"subagents": true`` enables the capability with the general-purpose subagent
and no custom specs.

Reliability lifecycle (M2.5.1)
------------------------------

The capability emits four lifecycle hooks the runner uses to feed the
``subagent_runs`` spine table:

* :func:`on_child_start` registers a SubAgentRun + initial heartbeat.
* :func:`on_child_heartbeat` bumps ``last_heartbeat_at`` every ~30s
  while the child is in flight; the 60-second ``reap_zombies`` cron
  fires when this falls more than 5 minutes behind.
* :func:`on_child_complete` runs the hallucination gate (aux LLM) on
  the child's final output. Below threshold → files an Approval
  (``resource_type='subagent_hallucination_review'``) and parks the
  run; above threshold → COMPLETED.
* :func:`on_child_failed` records the failure + optionally consumes
  retry budget so a thrashing child cannot loop forever.

These helpers are intentionally **runner-callable** (not bound to a
specific kernel callback signature); the native runner wires them in
from its child fan-out site once M2.5.6 batch-spawn lands. They open
their own DB session via :func:`app.db.session.get_session_factory`
so the chat WS turn task never has to share a SQLAlchemy session with
the lifecycle bookkeeping.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

DEFAULT_SPECS: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "description": (
            "Dedicated researcher. Good for focused web investigations: "
            "search → fetch → summarize. Uses web_search, web_fetch, and "
            "calculator/current_time as needed."
        ),
    },
    {
        "name": "coder",
        "description": (
            "Writes or reviews code. Uses filesystem tools (read/write/list/search) "
            "and current_time. Keeps code concise and idiomatic."
        ),
    },
]


def build_subagent_capability(
    *,
    policy: dict[str, Any] | None,
    primary_model: Any,
) -> Any | None:
    """Return a ``SubAgentCapability`` instance, or ``None`` if disabled."""
    spec = _normalize_spec((policy or {}).get("subagents"))
    if spec is None:
        return None

    try:
        from subagents_pydantic_ai import (
            SubAgentCapability,
        )
    except ImportError:  # pragma: no cover
        log.info("subagents-pydantic-ai not installed; delegation disabled")
        return None

    include_general = bool(spec.get("include_general_purpose", True))
    max_depth = int(spec.get("max_nesting_depth", 0))
    raw_specs = spec.get("specs")
    if not isinstance(raw_specs, list):
        raw_specs = DEFAULT_SPECS if spec.get("use_defaults", True) else []

    subagent_configs = [_make_config(s) for s in raw_specs if isinstance(s, dict)]

    try:
        return SubAgentCapability(
            subagents=subagent_configs or None,
            default_model=primary_model,
            include_general_purpose=include_general,
            max_nesting_depth=max_depth,
        )
    except Exception as e:  # pragma: no cover
        log.warning("SubAgentCapability init failed: %s", e)
        return None


def _normalize_spec(value: Any) -> dict[str, Any] | None:
    """Coerce several truthy forms into a dict spec, or None if disabled."""
    if value is None or value is False:
        return None
    if value is True:
        return {"enabled": True}
    if isinstance(value, dict):
        if value.get("enabled") is False:
            return None
        return value
    if isinstance(value, list):
        return {"enabled": True, "specs": value}
    return None


def _make_config(spec: dict[str, Any]) -> dict[str, Any]:
    """Coerce a loose dict into a ``SubAgentConfig``-shaped entry.

    The upstream ``SubAgentConfig`` TypedDict requires both
    ``description`` and ``instructions``. We fill ``instructions`` from
    the description when callers don't supply one explicitly.
    """
    name = str(spec.get("name") or "worker")
    description = str(spec.get("description") or f"Specialist named {name}.")
    instructions = str(
        spec.get("instructions")
        or description
        or f"You are {name}. Do the task you are assigned, then report back."
    )
    cfg: dict[str, Any] = {
        "name": name,
        "description": description,
        "instructions": instructions,
    }
    for k in (
        "model",
        "can_ask_questions",
        "max_questions",
        "preferred_mode",
        "typical_complexity",
        "typically_needs_context",
        "context_files",
    ):
        if k in spec:
            cfg[k] = spec[k]
    return cfg


# ─── M2.5.1 lifecycle hooks ──────────────────────────────────
async def on_child_start(
    *,
    workspace_id: uuid.UUID,
    parent_run_id: uuid.UUID,
    child_run_id: uuid.UUID,
    spawn_depth: int = 0,
    parent_session_id: uuid.UUID | None = None,
    retry_budget: int = 3,
) -> None:
    """Register the SubAgentRun spine row + transition RUNNING.

    Called by the runner immediately after spawning a child; the
    capability itself doesn't see the workspace/session context, so
    the runner is the right caller. Best-effort — a failed register
    falls back to logging so the chat run never breaks because the
    reliability spine is degraded.
    """
    from app.db.session import get_session_factory
    from app.services import subagent_run as svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            await svc.register_run(
                db,
                workspace_id=workspace_id,
                parent_run_id=parent_run_id,
                child_run_id=child_run_id,
                spawn_depth=spawn_depth,
                parent_session_id=parent_session_id,
                retry_budget=retry_budget,
            )
            await db.commit()
    except Exception:  # pragma: no cover - reliability is best-effort
        log.exception(
            "subagent on_child_start failed (parent=%s child=%s)",
            parent_run_id,
            child_run_id,
        )


async def on_child_heartbeat(
    *,
    child_run_id: uuid.UUID,
) -> bool:
    """Bump ``last_heartbeat_at`` on the spine row.

    Returns True when a row was updated, False otherwise. Designed to
    be called every :data:`HEARTBEAT_INTERVAL_SECONDS` (30s) — the
    60-second reaper fires once the value falls more than 5 minutes
    behind. Best-effort.
    """
    from app.db.session import get_session_factory
    from app.services import subagent_run as svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            ok = await svc.update_heartbeat(db, child_run_id=child_run_id)
            await db.commit()
            return ok
    except Exception:  # pragma: no cover - reliability is best-effort
        log.exception("subagent on_child_heartbeat failed (child=%s)", child_run_id)
        return False


async def on_child_complete(
    *,
    workspace_id: uuid.UUID,
    child_run_id: uuid.UUID,
    final_output: str,
    skip_hallucination_gate: bool = False,
) -> Literal["passed", "approval_required", "missing"]:
    """Drive the hallucination gate for a finished child.

    ``skip_hallucination_gate=True`` lets the runner short-circuit the
    aux LLM call when the policy explicitly disables the gate (testing
    / cost-sensitive deployments). Returns ``"missing"`` when the
    spine row doesn't exist (race against the reaper).
    """
    from app.db.session import get_session_factory
    from app.services import subagent_run as svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            from app.repositories.subagent_run import (
                SubAgentRunRepository,
            )

            child = await SubAgentRunRepository(db).get_by_child_run_id(child_run_id=child_run_id)
            if child is None:
                log.info(
                    "subagent on_child_complete: no spine row for child=%s",
                    child_run_id,
                )
                return "missing"

            if skip_hallucination_gate:
                await svc.transition_state(
                    db,
                    child_run_id=child_run_id,
                    target_state=svc.SubAgentRunState.COMPLETED,
                    reason="hallucination gate disabled by policy",
                    final_output=final_output,
                )
                await db.commit()
                return "passed"

            outcome = await svc.gate_hallucination_or_approve(
                db,
                workspace_id=workspace_id,
                child_run=child,
                final_output=final_output,
            )
            await db.commit()
            return outcome
    except Exception:  # pragma: no cover - reliability is best-effort
        log.exception("subagent on_child_complete failed (child=%s)", child_run_id)
        return "missing"


async def on_child_failed(
    *,
    child_run_id: uuid.UUID,
    error_kind: str,
    consume_budget: bool = False,
) -> int | None:
    """Record the failure + optionally consume retry budget.

    Returns the remaining budget when ``consume_budget=True`` (or
    ``None`` when the caller didn't ask for budget bookkeeping or the
    spine row is missing). Raises :class:`RetryBudgetExhausted` from
    the underlying service when the budget is already 0 — caller is
    expected to terminate the parent's retry loop.
    """
    from app.db.session import get_session_factory
    from app.services import subagent_run as svc

    factory = get_session_factory()
    remaining: int | None = None
    try:
        async with factory() as db:
            await svc.transition_state(
                db,
                child_run_id=child_run_id,
                target_state=svc.SubAgentRunState.FAILED,
                reason=f"child failed: {error_kind}",
                error_kind=error_kind,
            )
            if consume_budget:
                remaining = await svc.consume_retry_budget(db, child_run_id=child_run_id)
            await db.commit()
    except svc.RetryBudgetExhausted:
        # Budget audit + commit happened inside consume_retry_budget;
        # surface to caller so the parent stops retrying.
        raise
    except Exception:  # pragma: no cover - reliability is best-effort
        log.exception("subagent on_child_failed failed (child=%s)", child_run_id)
    return remaining


# ─── M2.5.6 batch spawn ──────────────────────────────────────
# Audit action keys (single source of truth for tests + dashboards).
AUDIT_BATCH_STARTED = "subagent.batch_started"
AUDIT_BATCH_COMPLETED = "subagent.batch_completed"
AUDIT_BATCH_SERIAL_FALLBACK = "subagent.batch_serial_fallback"
AUDIT_NESTING_DEPTH_EXCEEDED = "subagent.nesting_depth_exceeded"

# Outer wall used when a caller doesn't pin one. Mirrors the M2.2 evolver
# 5-minute hard limit so any hang above the heartbeat threshold (5 min)
# still fires the timeout path before the reaper would notice.
DEFAULT_TASK_TIMEOUT_SECONDS = 300

SubAgentResultStatus = Literal[
    "completed",
    "failed",
    "timeout",
    "halluc_review",
    "cancelled",
    "rejected",
]


class BatchDisabled(RuntimeError):
    """Raised by :func:`delegate_batch` when the workspace turned the
    batch capability off. Callers map to a structured tool result.
    """

    code = "subagent.batch_disabled"


class NestingDepthExceeded(RuntimeError):
    """Raised when ``spawn_depth`` would meet or exceed the configured
    ``max_nesting_depth``. The caller is expected to surface this to
    the parent agent as a structured rejection — recursive batching
    must terminate cleanly before the spine table fills with depth-N
    fan-out.
    """

    code = "subagent.nesting_depth_exceeded"


@dataclass(slots=True)
class SubAgentTask:
    """One task in a batch.

    ``task_id`` is caller-defined and only needs uniqueness within the
    enclosing batch; the runtime's ``child_run_id`` is the durable
    identifier (and is what the spine table indexes). ``inherit_skills``
    / ``inherit_memory`` mirror the privacy contract: skills are
    workspace-scoped configuration so children always see them, but
    long-term memory often carries personal preferences and defaults to
    *not* leaking into spawned helpers.
    """

    task_id: str
    prompt: str
    target_agent_id: uuid.UUID
    timeout_seconds: int = DEFAULT_TASK_TIMEOUT_SECONDS
    inherit_skills: bool = True
    inherit_memory: bool = False


@dataclass(slots=True)
class SubAgentResult:
    """Outcome of one child within a batch.

    ``status`` is the canonical state the parent reasons about;
    ``error_kind`` mirrors :attr:`SubAgentRun.error_kind` so the parent
    can decide whether to retry on the next batch turn (the M2.5.1
    ``retry_budget`` machinery is owned by the spine table — this
    field is purely informational from the parent's perspective).
    """

    task_id: str
    child_run_id: uuid.UUID
    status: SubAgentResultStatus
    output: str | None = None
    error_kind: str | None = None
    duration_ms: int | None = None
    proposals_created: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["child_run_id"] = str(self.child_run_id)
        return d


class BatchSpawnResult(BaseModel):
    """Aggregate envelope returned to the parent agent.

    ``results`` is keyed on the caller-supplied :attr:`SubAgentTask.task_id`
    so the parent can reason in its own naming scheme. Tally fields
    mirror the canonical statuses so dashboards don't have to walk
    every result row.
    """

    parent_run_id: uuid.UUID
    total: int
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    halluc_review: int = 0
    cancelled: int = 0
    rejected: int = 0
    results: dict[str, SubAgentResult] = Field(default_factory=dict)
    duration_ms: int = 0
    serial_fallback: bool = False
    serial_fallback_reason: str | None = None
    max_concurrent_used: int = 1


# ─── helpers ────────────────────────────────────────────────
def _validate_unique_task_ids(tasks: list[SubAgentTask]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(
                f"duplicate task_id {task.task_id!r} in batch; "
                "task ids must be unique within a single delegate_batch call"
            )
        seen.add(task.task_id)


async def _audit(
    *,
    workspace_id: uuid.UUID,
    action: str,
    summary: str,
    metadata: dict[str, Any],
    actor_identity_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
) -> None:
    """Open a fresh session for one best-effort audit row."""
    from app.db.session import get_session_factory
    from app.services import audit as audit_svc

    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="subagent_batch",
                resource_id=resource_id,
                summary=summary,
                metadata=metadata,
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit is best-effort
        log.exception("subagent batch audit failed action=%s", action)


async def _resolve_child_agent_model(
    *,
    workspace_id: uuid.UUID,
    target_agent_id: uuid.UUID,
) -> tuple[Any | None, Any | None]:
    """Best-effort: resolve the pydantic-ai model + the agent ORM row.

    Returns ``(model, agent_orm)`` — either component may be ``None``
    when the workspace is missing a provider or the agent row is gone.
    The runner-style fallback is a no-op test stub: callers handle
    ``None`` model by treating the spawn as a failed dispatch.
    """
    from app.agents.kernels.model_client import (
        build_pydantic_ai_model,
        resolve_for_agent,
    )
    from app.db.models.agent import Agent as AgentModel
    from app.db.session import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as db:
            agent_orm = await db.get(AgentModel, target_agent_id)
            if agent_orm is None or agent_orm.workspace_id != workspace_id:
                return None, None
            resolved = await resolve_for_agent(workspace_id=workspace_id, agent_id=target_agent_id)
            if resolved is None:
                return None, agent_orm
            return build_pydantic_ai_model(resolved), agent_orm
    except Exception:  # pragma: no cover — resolution is best-effort
        log.exception(
            "child model resolution failed agent=%s ws=%s",
            target_agent_id,
            workspace_id,
        )
        return None, None


def _build_child_agent(*, model: Any, persona_md: str | None) -> Any:
    """Construct a fresh pydantic-ai ``Agent`` for one child run.

    The agent is intentionally toolless at this layer — the M2.5.6
    contract is "hand the prompt to the child model and capture its
    final answer". Tool composition (skills, memory, sub-agents nested
    again) is the runner's job; the batch helper stays focused on
    parallel dispatch + reliability bookkeeping. Future iterations may
    extend the signature with ``inherit_skills`` honouring callers.
    """
    from pydantic_ai import Agent

    return Agent(
        model=model,
        system_prompt=(persona_md or "You are a focused worker. Complete the task."),
    )


def _extract_final_text(run_result: Any) -> str:
    """Coerce the pydantic-ai run result envelope into plain text."""
    if run_result is None:
        return ""
    output = getattr(run_result, "output", None)
    if output is None:
        output = getattr(run_result, "data", None)
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return str(output)


def _proposals_for_run_count(run_result: Any) -> int:
    """How many Approval rows did this child file? Default 0.

    A child agent that called ``propose_skill_*`` or other approval-
    writing verbs would surface a count via its tool result envelope;
    today we don't have a portable carrier so the field stays 0 unless
    a future iteration plumbs the M2.7 propose audit chain into the
    child run summary.
    """
    if run_result is None:
        return 0
    candidate = getattr(run_result, "proposals_created", None)
    if isinstance(candidate, int) and candidate >= 0:
        return candidate
    return 0


# ─── per-child runner ───────────────────────────────────────
async def _run_single_child(
    *,
    parent_run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    parent_session_id: uuid.UUID | None,
    parent_identity_id: uuid.UUID | None,
    task: SubAgentTask,
    spawn_depth: int,
    skip_hallucination_gate: bool = False,
) -> SubAgentResult:
    """Run one child task end-to-end.

    Order of operations:

    1. Mint ``child_run_id`` and register the spine row (M2.5.1).
    2. Resolve the target agent's model. Missing → record failure +
       return ``status='rejected'``.
    3. Build a stub pydantic-ai agent and run with a hard
       :func:`asyncio.wait_for` outer wall (default 5 min, capped per
       task by :attr:`SubAgentTask.timeout_seconds`).
    4. On success: route the output through the M2.5.1 hallucination
       gate; outcome decides ``status='completed'`` vs ``halluc_review``.
    5. On timeout / cancel / failure: drive the spine row to the
       matching terminal state via the existing lifecycle hooks.

    Best-effort throughout: any exception below the wait_for boundary
    is captured into a ``status='failed'`` result so a single child
    crash never propagates out of the gather() call.
    """
    child_run_id = uuid.uuid4()
    started = time.perf_counter()

    await on_child_start(
        workspace_id=workspace_id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        spawn_depth=int(spawn_depth),
        parent_session_id=parent_session_id,
    )

    model, agent_orm = await _resolve_child_agent_model(
        workspace_id=workspace_id,
        target_agent_id=task.target_agent_id,
    )

    if model is None:
        error_kind = "agent_not_found" if agent_orm is None else "no_aux_model"
        await on_child_failed(child_run_id=child_run_id, error_kind=error_kind)
        return SubAgentResult(
            task_id=task.task_id,
            child_run_id=child_run_id,
            status="rejected",
            error_kind=error_kind,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    persona = getattr(agent_orm, "persona_md", None) if agent_orm is not None else None
    child_agent = _build_child_agent(model=model, persona_md=persona)

    timeout = max(1, int(task.timeout_seconds))
    final_text = ""
    proposals_created = 0
    try:
        run_result = await asyncio.wait_for(child_agent.run(task.prompt), timeout=timeout)
        final_text = _extract_final_text(run_result)
        proposals_created = _proposals_for_run_count(run_result)
    except TimeoutError:
        await on_child_failed(child_run_id=child_run_id, error_kind="timeout")
        # Match the M2.5.1 reaper contract: a hard timeout flips the
        # row to ZOMBIE only when the heartbeat has actually died; for
        # a wait_for boundary we know the child never had a chance to
        # respond, so FAILED + ``error_kind=timeout`` is the right
        # surface. The reaper handles the ZOMBIE edge if the child
        # zombifies after we time out.
        return SubAgentResult(
            task_id=task.task_id,
            child_run_id=child_run_id,
            status="timeout",
            error_kind="timeout",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except asyncio.CancelledError:
        await on_child_failed(child_run_id=child_run_id, error_kind="cancelled")
        return SubAgentResult(
            task_id=task.task_id,
            child_run_id=child_run_id,
            status="cancelled",
            error_kind="cancelled",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        error_kind = type(exc).__name__[:80]
        log.exception(
            "subagent child crashed task_id=%s child_run_id=%s",
            task.task_id,
            child_run_id,
        )
        await on_child_failed(child_run_id=child_run_id, error_kind=error_kind)
        return SubAgentResult(
            task_id=task.task_id,
            child_run_id=child_run_id,
            status="failed",
            error_kind=error_kind,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # Success path — drive the hallucination gate. The gate runs per
    # child independently so a slow aux call on one sibling never
    # blocks the others (each child holds its own wait inside the
    # gather()). Any internal failure of the gate falls back to
    # ``"missing"`` which we surface as ``completed`` with no score —
    # the gate already wrote a fail-open audit in that case.
    gate_outcome = await on_child_complete(
        workspace_id=workspace_id,
        child_run_id=child_run_id,
        final_output=final_text,
        skip_hallucination_gate=skip_hallucination_gate,
    )
    if gate_outcome == "approval_required":
        status: SubAgentResultStatus = "halluc_review"
    else:
        status = "completed"

    return SubAgentResult(
        task_id=task.task_id,
        child_run_id=child_run_id,
        status=status,
        output=final_text or None,
        duration_ms=int((time.perf_counter() - started) * 1000),
        proposals_created=int(proposals_created),
    )


# ─── batch entry-point ──────────────────────────────────────
async def delegate_batch(
    *,
    parent_run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    parent_session_id: uuid.UUID | None,
    parent_identity_id: uuid.UUID | None,
    tasks: list[SubAgentTask],
    max_concurrent: int | None = None,
    spawn_depth: int = 1,
    skip_hallucination_gate: bool = False,
) -> BatchSpawnResult:
    """Fan out ``tasks`` as parallel sub-agent runs.

    Behaviour contract:

    * Reads workspace policy via
      :func:`app.services.subagent_batch_config.get_workspace_subagent_batch_config`.
    * ``batch_enabled=False`` or ``max_batch_size=1`` → degrade to
      serial single-child loop. Each iteration still uses the same
      spine + gate plumbing so observability stays uniform.
    * ``spawn_depth >= max_nesting_depth`` → reject before any spine
      row is written; the parent receives a populated
      :class:`BatchSpawnResult` with ``rejected == total``.
    * Otherwise: ``asyncio.Semaphore(min(max_concurrent, len(tasks)))``
      caps the in-flight count and ``asyncio.gather(return_exceptions=True)``
      makes sure one sibling failure cannot abort the rest.

    Audit chain: ``subagent.batch_started`` on entry,
    ``subagent.batch_completed`` (or ``subagent.batch_serial_fallback``
    / ``subagent.nesting_depth_exceeded``) on exit. Each child still
    emits its own state-machine audits (``subagent.run_registered``,
    ``subagent.state_transitioned``, gate audits) via the M2.5.1
    plumbing.
    """
    if not tasks:
        return BatchSpawnResult(
            parent_run_id=parent_run_id,
            total=0,
            duration_ms=0,
            max_concurrent_used=0,
        )

    _validate_unique_task_ids(tasks)

    started = time.perf_counter()

    config = await _load_resolved_config(workspace_id=workspace_id)

    # Nesting depth gate — fail fast before we register any spine row,
    # so the rejection path is deterministic and cheap.
    if int(spawn_depth) >= int(config.max_nesting_depth):
        await _audit(
            workspace_id=workspace_id,
            actor_identity_id=parent_identity_id,
            action=AUDIT_NESTING_DEPTH_EXCEEDED,
            summary=(
                f"subagent batch rejected: spawn_depth={spawn_depth} "
                f"exceeds max_nesting_depth={config.max_nesting_depth}"
            ),
            metadata={
                "parent_run_id": str(parent_run_id),
                "task_count": len(tasks),
                "spawn_depth": int(spawn_depth),
                "max_nesting_depth": int(config.max_nesting_depth),
            },
        )
        results = {
            task.task_id: SubAgentResult(
                task_id=task.task_id,
                child_run_id=uuid.uuid4(),
                status="rejected",
                error_kind="nesting_depth_exceeded",
                duration_ms=0,
            )
            for task in tasks
        }
        return BatchSpawnResult(
            parent_run_id=parent_run_id,
            total=len(tasks),
            rejected=len(tasks),
            results=results,
            duration_ms=int((time.perf_counter() - started) * 1000),
            max_concurrent_used=0,
        )

    # Cap the request to the workspace's max_batch_size. Over-quota
    # tasks are not silently dropped — they're surfaced as ``rejected``
    # so the parent agent learns to size future batches correctly.
    max_batch_size = max(1, int(config.max_batch_size))
    accepted_tasks: list[SubAgentTask]
    over_quota_tasks: list[SubAgentTask]
    if len(tasks) > max_batch_size:
        accepted_tasks = list(tasks[:max_batch_size])
        over_quota_tasks = list(tasks[max_batch_size:])
    else:
        accepted_tasks = list(tasks)
        over_quota_tasks = []

    # Decide between parallel and serial paths. Serial fallback is the
    # explicit "downgrade" lever — it still uses the same per-child
    # plumbing so observability is identical to the parallel path.
    serial_fallback = False
    serial_reason: str | None = None
    if not config.batch_enabled:
        serial_fallback = True
        serial_reason = "batch_disabled"
    elif max_batch_size <= 1:
        serial_fallback = True
        serial_reason = "max_batch_size_one"
    elif len(accepted_tasks) <= 1:
        # One-task batches don't benefit from a semaphore; we still
        # log them as serial so the audit shape is consistent.
        serial_fallback = True
        serial_reason = "single_task"

    # Effective concurrency cap. Caller override is bounded by the
    # workspace policy upper bound so an aggressive parent can't bypass
    # the platform-wide ceiling.
    requested = (
        max(1, int(max_concurrent)) if max_concurrent is not None else int(config.max_concurrent)
    )
    effective_max_concurrent = max(1, min(requested, int(config.max_concurrent)))
    if serial_fallback:
        effective_max_concurrent = 1

    # ── Audit batch start ────────────────────────────────────
    if serial_fallback:
        await _audit(
            workspace_id=workspace_id,
            actor_identity_id=parent_identity_id,
            action=AUDIT_BATCH_SERIAL_FALLBACK,
            summary=(
                f"subagent batch fell back to serial: {serial_reason} (tasks={len(accepted_tasks)})"
            ),
            metadata={
                "parent_run_id": str(parent_run_id),
                "task_count": len(accepted_tasks),
                "reason": serial_reason,
                "max_batch_size": max_batch_size,
                "spawn_depth": int(spawn_depth),
            },
        )
    await _audit(
        workspace_id=workspace_id,
        actor_identity_id=parent_identity_id,
        action=AUDIT_BATCH_STARTED,
        summary=(
            f"subagent batch started parent_run={parent_run_id} "
            f"tasks={len(accepted_tasks)} max_concurrent={effective_max_concurrent} "
            f"depth={spawn_depth} serial={serial_fallback}"
        ),
        metadata={
            "parent_run_id": str(parent_run_id),
            "task_count": len(accepted_tasks),
            "rejected_over_quota": len(over_quota_tasks),
            "max_concurrent": int(effective_max_concurrent),
            "max_batch_size": max_batch_size,
            "max_nesting_depth": int(config.max_nesting_depth),
            "spawn_depth": int(spawn_depth),
            "serial_fallback": bool(serial_fallback),
            "serial_fallback_reason": serial_reason,
        },
    )

    # ── Run children ─────────────────────────────────────────
    semaphore = asyncio.Semaphore(effective_max_concurrent)

    async def _bounded(task: SubAgentTask) -> SubAgentResult:
        async with semaphore:
            return await _run_single_child(
                parent_run_id=parent_run_id,
                workspace_id=workspace_id,
                parent_session_id=parent_session_id,
                parent_identity_id=parent_identity_id,
                task=task,
                spawn_depth=spawn_depth,
                skip_hallucination_gate=skip_hallucination_gate,
            )

    # ``return_exceptions=True`` is the contract that lets one child's
    # crash never abort the rest. Every coroutine inside ``_bounded``
    # already wraps its own try/except into a SubAgentResult, so under
    # normal operation gather() resolves all entries to results — but
    # we keep the safety net for unforeseen runtime errors (e.g. an
    # asyncio internal raise).
    coros = [_bounded(task) for task in accepted_tasks]
    raw = await asyncio.gather(*coros, return_exceptions=True)

    results: dict[str, SubAgentResult] = {}
    for task, item in zip(accepted_tasks, raw, strict=True):
        if isinstance(item, BaseException):
            log.exception(
                "subagent batch unexpected gather exception task_id=%s",
                task.task_id,
                exc_info=item,
            )
            results[task.task_id] = SubAgentResult(
                task_id=task.task_id,
                child_run_id=uuid.uuid4(),
                status="failed",
                error_kind=type(item).__name__[:80],
            )
        else:
            results[task.task_id] = item

    for over in over_quota_tasks:
        results[over.task_id] = SubAgentResult(
            task_id=over.task_id,
            child_run_id=uuid.uuid4(),
            status="rejected",
            error_kind="batch_size_exceeded",
        )

    # ── Tally + final audit ─────────────────────────────────
    completed = sum(1 for r in results.values() if r.status == "completed")
    failed = sum(1 for r in results.values() if r.status == "failed")
    timed_out = sum(1 for r in results.values() if r.status == "timeout")
    halluc_review = sum(1 for r in results.values() if r.status == "halluc_review")
    cancelled = sum(1 for r in results.values() if r.status == "cancelled")
    rejected = sum(1 for r in results.values() if r.status == "rejected")
    duration_ms = int((time.perf_counter() - started) * 1000)

    summary = BatchSpawnResult(
        parent_run_id=parent_run_id,
        total=len(results),
        completed=completed,
        failed=failed,
        timed_out=timed_out,
        halluc_review=halluc_review,
        cancelled=cancelled,
        rejected=rejected,
        results=results,
        duration_ms=duration_ms,
        serial_fallback=serial_fallback,
        serial_fallback_reason=serial_reason,
        max_concurrent_used=int(effective_max_concurrent),
    )

    await _audit(
        workspace_id=workspace_id,
        actor_identity_id=parent_identity_id,
        action=AUDIT_BATCH_COMPLETED,
        summary=(
            f"subagent batch completed parent_run={parent_run_id} "
            f"completed={completed} failed={failed} timed_out={timed_out} "
            f"halluc_review={halluc_review} rejected={rejected} "
            f"duration_ms={duration_ms}"
        ),
        metadata={
            "parent_run_id": str(parent_run_id),
            "total": int(summary.total),
            "completed": int(summary.completed),
            "failed": int(summary.failed),
            "timed_out": int(summary.timed_out),
            "halluc_review": int(summary.halluc_review),
            "cancelled": int(summary.cancelled),
            "rejected": int(summary.rejected),
            "duration_ms": int(summary.duration_ms),
            "serial_fallback": bool(serial_fallback),
            "serial_fallback_reason": serial_reason,
            "max_concurrent_used": int(effective_max_concurrent),
            "spawn_depth": int(spawn_depth),
        },
    )
    return summary


async def delegate_task(
    *,
    parent_run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    parent_session_id: uuid.UUID | None,
    parent_identity_id: uuid.UUID | None,
    prompt: str,
    target_agent_id: uuid.UUID,
    timeout_seconds: int = DEFAULT_TASK_TIMEOUT_SECONDS,
    inherit_skills: bool = True,
    inherit_memory: bool = False,
    spawn_depth: int = 1,
    task_id: str | None = None,
    skip_hallucination_gate: bool = False,
) -> SubAgentResult:
    """Single-child convenience wrapper.

    Internally builds a one-task batch and unwraps the result. Kept as
    a separate name so back-compat callers + tools that semantically
    delegate "one task" don't have to learn the batch shape just to
    spawn a single child.
    """
    chosen_task_id = task_id or "task-0"
    task = SubAgentTask(
        task_id=chosen_task_id,
        prompt=prompt,
        target_agent_id=target_agent_id,
        timeout_seconds=int(timeout_seconds),
        inherit_skills=bool(inherit_skills),
        inherit_memory=bool(inherit_memory),
    )
    summary = await delegate_batch(
        parent_run_id=parent_run_id,
        workspace_id=workspace_id,
        parent_session_id=parent_session_id,
        parent_identity_id=parent_identity_id,
        tasks=[task],
        max_concurrent=1,
        spawn_depth=spawn_depth,
        skip_hallucination_gate=skip_hallucination_gate,
    )
    return summary.results[chosen_task_id]


async def _load_resolved_config(*, workspace_id: uuid.UUID) -> Any:
    """Open a fresh session to read the merged batch config.

    Returns a :class:`ResolvedSubagentBatchConfig` instance; the import
    is local so the harness module can stay free of a hard dependency
    on the service layer's import order.
    """
    from app.db.session import get_session_factory
    from app.services.subagent_batch_config import (
        ResolvedSubagentBatchConfig,
        get_workspace_subagent_batch_config,
    )

    factory = get_session_factory()
    try:
        async with factory() as db:
            return await get_workspace_subagent_batch_config(db, workspace_id=workspace_id)
    except Exception:  # pragma: no cover — fail-open to defaults
        log.exception(
            "subagent batch config load failed ws=%s; using schema defaults",
            workspace_id,
        )
        from app.schemas.platform_settings import (
            SubagentBatchDefaults,
        )

        return ResolvedSubagentBatchConfig.from_defaults(SubagentBatchDefaults())
