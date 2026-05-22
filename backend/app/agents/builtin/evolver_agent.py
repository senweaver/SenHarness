"""Platform-builtin evolver subagent (M2.2).

The evolver is a *one-shot* pydantic-ai agent invoked by a workflow
runner (M2.3) or an admin endpoint to look at recent low-scoring
session artifacts and decide whether any SkillPack in the workspace
should be created or improved. It never appears in the
``agents`` table — every workspace shares the same builtin definition,
loaded from disk and parameterised by the per-workspace
:class:`~app.schemas.platform_settings.EvolverSettings`.

Toolset (all gated by ``available_for_kinds=("evolver",)``):

* M2.1 + M2.7 propose verbs:
    propose_skill_create / patch / edit / delete /
    write_file / remove_file
* M2.2 helpers:
    list_session_artifacts / read_skill_pack / mark_skip

Reliability invariants (roadmap principle "subagent must not silently
hang"):

* :data:`EVOLVER_AGENT_TIMEOUT_SECONDS` is a hard outer wall. The
  invoker wraps the model run in :func:`asyncio.wait_for`; a timeout
  rolls forward to an ``evolver.subagent_timeout`` audit + a breaker
  bump.
* The Redis breaker key ``evolver:fail:<workspace_id>`` is shared
  with the M2.1 / M2.7 propose verbs — when the propose pipeline
  itself misbehaves the breaker trips and the next admin invoke
  short-circuits with ``EvolverBreakerOpenError`` rather than spinning
  another five-minute timeout window.
* Aux model resolution falls through
  :data:`EvolverSettings.aux_model_evolver` →
  ``aux_model_skill_review`` →
  ``aux_model_judge`` → workspace's first enabled chat model. A
  workspace with zero providers configured fails fast with
  ``EvolverAuxModelMissingError``.

The invoker returns an :class:`EvolverInvokeResult` regardless of
outcome (success / skip / timeout / error) so callers can tee the
struct into their own audit / job-result tables without matching on
exception types.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from app.agents.auxiliary_client import (
    AuxiliaryConfig,
    AuxiliaryTask,
    get_aux_model,
)
from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
    parse_override,
    resolve_for_workspace,
)
from app.agents.tools import BUILTIN_TOOL_REGISTRY, BuiltinTool
from app.agents.tools._context import ToolRunContext, set_context
from app.db.models.approval import Approval
from app.db.session import get_session_factory
from app.jobs._breaker import bump_failure, is_breaker_open
from app.repositories.approval import ApprovalRepository
from app.schemas.platform_settings import EvolverSettings
from app.services import audit as audit_svc
from app.services.evolver_config import get_workspace_evolver_config

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_FAILED",
    "AUDIT_INVOKED",
    "AUDIT_SUBAGENT_COMPLETED",
    "AUDIT_TIMEOUT",
    "EVOLVER_AGENT_KIND",
    "EVOLVER_AGENT_TIMEOUT_SECONDS",
    "EVOLVER_BREAKER_BUCKET",
    "EVOLVER_TOOL_NAMES",
    "EvolverAuxModelMissingError",
    "EvolverBreakerOpenError",
    "EvolverDisabledError",
    "EvolverError",
    "EvolverInvokeResult",
    "build_evolver_agent",
    "invoke_evolver_subagent",
    "load_evolver_persona",
]


# ─── Constants ───────────────────────────────────────────────
EVOLVER_AGENT_KIND = "evolver"
EVOLVER_AGENT_TIMEOUT_SECONDS = 300
EVOLVER_BREAKER_BUCKET = "evolver"

# Audit action keys (verbatim — referenced by tests + dashboards).
AUDIT_INVOKED = "evolver.subagent_invoked"
AUDIT_SUBAGENT_COMPLETED = "evolver.subagent_completed"
AUDIT_TIMEOUT = "evolver.subagent_timeout"
AUDIT_FAILED = "evolver.subagent_failed"

# The exact tool catalogue the evolver is allowed to register. Order
# matches the rough decision flow (read → mutate → halt) so a
# debugger printing the registered names sees the natural arc.
EVOLVER_TOOL_NAMES: tuple[str, ...] = (
    "list_session_artifacts",
    "read_skill_pack",
    "propose_skill_create",
    "propose_skill_patch",
    "propose_skill_edit",
    "propose_skill_delete",
    "propose_skill_write_file",
    "propose_skill_remove_file",
    "mark_skip",
)


# ─── Errors ──────────────────────────────────────────────────
class EvolverError(RuntimeError):
    """Base class for invoker pre-flight failures.

    These bubble up to the caller (admin endpoint / ARQ job) so the
    operator sees a deterministic short-circuit instead of an
    accidental five-minute timeout. ``code`` is stable for i18n.
    """

    code: str = "evolver.error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class EvolverDisabledError(EvolverError):
    code = "evolver.disabled"


class EvolverBreakerOpenError(EvolverError):
    code = "evolver.breaker_tripped"


class EvolverAuxModelMissingError(EvolverError):
    code = "evolver.aux_model_missing"


# ─── Result struct ───────────────────────────────────────────
@dataclass(slots=True)
class EvolverInvokeResult:
    """Outcome envelope returned by :func:`invoke_evolver_subagent`.

    ``proposals_created`` is computed by counting the Approval rows
    written with this run's ``run_id``, not by parsing the agent's
    self-report — the source of truth is the persistence layer.
    ``error`` is set on every non-happy path (timeout, internal
    exception, missing aux model). When an exception is raised
    pre-flight (``EvolverDisabledError`` etc.) the caller never sees this
    struct; the result is reserved for "the agent ran" outcomes.
    """

    run_id: uuid.UUID
    proposals_created: int
    skipped: bool
    duration_ms: int
    final_message: str | None
    error: str | None = None
    aux_model: str | None = None
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["run_id"] = str(self.run_id)
        return d


# ─── Persona loader ──────────────────────────────────────────
@cache
def load_evolver_persona() -> str:
    """Load the persona markdown from disk (cached process-wide).

    Defensive fallback returns a one-line stub so a missing file
    cannot brick the subagent in production; the brief explicitly
    requires the file though, so missing-file in practice means a
    mispackaged container.
    """
    path = Path(__file__).resolve().parent.parent / "templates" / "evolver_persona.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:  # pragma: no cover - dev sanity only
        log.error("evolver_persona.md missing at %s", path)
        return "You are the workspace skill curator. Review the artifacts and propose improvements."


# ─── Aux model resolution ────────────────────────────────────
async def _resolve_aux_config(
    *, workspace_id: uuid.UUID, evolver_config: EvolverSettings
) -> AuxiliaryConfig | None:
    """Pick a single aux config by walking the documented fall-through.

    Order:
        1. ``EvolverSettings.aux_model_evolver`` (workspace-overridable
           explicit pin).
        2. ``aux_model_skill_review`` via :func:`get_aux_model` (which
           also covers ``aux_model_default`` + workspace's first enabled
           chat model).
        3. ``aux_model_judge`` via :func:`get_aux_model` (covers
           operators who only configured the judge tier).
        4. ``None`` — the caller raises :class:`EvolverAuxModelMissingError`.
    """
    factory = get_session_factory()
    async with factory() as db:
        explicit = (evolver_config.aux_model_evolver or "").strip()
        if explicit:
            parsed = parse_override(explicit)
            if parsed is not None:
                if parsed.api_key is None:
                    fallback = await resolve_for_workspace(
                        workspace_id=workspace_id, kind=parsed.provider_kind
                    )
                    if fallback is not None:
                        parsed.api_key = fallback.api_key
                        parsed.base_url = parsed.base_url or fallback.base_url
                return AuxiliaryConfig(
                    task=AuxiliaryTask.SKILL_REVIEW,
                    model=f"{parsed.provider_kind}:{parsed.model_name}",
                    base_url=parsed.base_url,
                    api_key_ref=parsed.api_key,
                    extra={"_resolved": parsed, "_source": "evolver_pin"},
                )

        cfg = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.SKILL_REVIEW)
        if cfg is not None:
            return cfg

        cfg = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.JUDGE)
        return cfg


def _build_pydantic_ai_model_from_config(config: AuxiliaryConfig) -> Any:
    resolved = config.extra.get("_resolved") if config.extra else None
    if not isinstance(resolved, ResolvedModel):
        provider_kind, _, model_name = config.model.partition(":")
        if not provider_kind or not model_name:
            return None
        resolved = ResolvedModel(
            provider_kind=provider_kind,
            model_name=model_name,
            api_key=config.api_key_ref,
            base_url=config.base_url,
            source="override",
        )
    return build_pydantic_ai_model(resolved)


# ─── Tool registration ───────────────────────────────────────
def _register_builtin_tool_plain(agent: Any, tool: BuiltinTool) -> None:
    """Attach a :class:`BuiltinTool` to a pydantic-ai ``Agent``.

    Mirrors the helper inside the native runner — duplicated here so
    the evolver path is self-contained and the runner module stays
    private. The closure parses the args via the tool's pydantic
    model, dispatches to the runner (sync or async) and unwraps the
    result back to a plain dict.
    """
    args_model = tool.args_model
    runner = tool.runner
    name = tool.name
    description = tool.description

    async def _handler(**kwargs: Any) -> dict:
        parsed = args_model(**kwargs)
        result = runner(parsed)
        if asyncio.iscoroutine(result):
            return await result  # type: ignore[return-value]
        return result  # type: ignore[return-value]

    _handler.__name__ = name
    _handler.__doc__ = description
    _handler.__annotations__ = {
        field_name: (field.annotation or str)
        for field_name, field in args_model.model_fields.items()
    }
    _handler.__annotations__["return"] = dict

    agent.tool_plain(_handler)


def build_evolver_agent(*, model: Any) -> Any:
    """Construct a fresh pydantic-ai ``Agent`` for one invocation.

    A new instance per call avoids leaking a model handle between
    workspaces (each workspace can pin its own ``aux_model_evolver``)
    and keeps the agent stateless — the tools own all side effects.
    """
    from pydantic_ai import Agent

    agent = Agent(model=model, system_prompt=load_evolver_persona())

    missing: list[str] = []
    for name in EVOLVER_TOOL_NAMES:
        tool = BUILTIN_TOOL_REGISTRY.get(name)
        if tool is None:
            missing.append(name)
            continue
        _register_builtin_tool_plain(agent, tool)

    if missing:
        # Loud failure — the evolver cannot do its job with a partial
        # toolset, and a missing entry usually means a refactor broke
        # the registry import order.
        raise RuntimeError(f"Evolver tool registry incomplete; missing={missing!r}")

    return agent


# ─── Helpers ─────────────────────────────────────────────────
async def _count_proposals_for_run(*, workspace_id: uuid.UUID, run_id: uuid.UUID) -> int:
    factory = get_session_factory()
    async with factory() as db:
        repo = ApprovalRepository(db)
        stmt = select(Approval).where(
            Approval.workspace_id == workspace_id,
            Approval.run_id == run_id,
        )
        rows = (await db.execute(stmt)).scalars().all()
    _ = repo
    return len(list(rows))


async def _record_audit(
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    action: str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        await audit_svc.record(
            db,
            action=action,
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="workspace",
            resource_id=workspace_id,
            summary=summary,
            metadata=metadata,
        )
        await db.commit()


async def _bump_breaker(*, workspace_id: uuid.UUID, evolver_config: EvolverSettings) -> None:
    await bump_failure(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        window_seconds=int(evolver_config.evolver_breaker_window_seconds),
    )


def _initial_user_prompt(
    *,
    triggering_run_ids: list[uuid.UUID] | None,
    invocation_kind: str,
) -> str:
    if triggering_run_ids:
        run_count = len(triggering_run_ids)
        listed = ", ".join(str(r) for r in triggering_run_ids[:5])
        suffix = "" if run_count <= 5 else f" (+{run_count - 5} more)"
        return (
            f"You have {run_count} triggering run(s) flagged by the workflow "
            f"({invocation_kind} invocation): {listed}{suffix}. "
            "Use list_session_artifacts to confirm what failed, read_skill_pack "
            "on the relevant pack(s), then file a small set of proposals or "
            "call mark_skip if no change is warranted."
        )
    return (
        "Review the most recent low-scoring session artifacts in this "
        f"workspace ({invocation_kind} invocation). Start with "
        "list_session_artifacts (judge_score_max=0). Either file targeted "
        "proposals or call mark_skip if the batch is healthy."
    )


# ─── Entry point ─────────────────────────────────────────────
async def invoke_evolver_subagent(
    *,
    workspace_id: uuid.UUID,
    triggering_run_ids: list[uuid.UUID] | None = None,
    invocation_kind: Literal["scheduled", "manual"] = "scheduled",
    actor_identity_id: uuid.UUID | None = None,
    timeout_seconds: int | None = None,
) -> EvolverInvokeResult:
    """Run the evolver agent once and capture its outcome.

    Pre-flight failures (workspace disabled, breaker tripped, no aux
    model) raise :class:`EvolverError`; the caller must catch and map
    to its own response shape. Once the run starts every outcome
    (success, skip, timeout, internal exception) returns a populated
    :class:`EvolverInvokeResult` so the caller has uniform reporting.

    ``actor_identity_id`` should be the admin who fired the manual
    invoke; for scheduled runs it stays ``None`` and audit rows show
    a system-driven actor.
    """
    timeout = int(timeout_seconds or EVOLVER_AGENT_TIMEOUT_SECONDS)
    run_id = uuid.uuid4()
    started = time.perf_counter()

    factory = get_session_factory()
    async with factory() as db:
        evolver_config = await get_workspace_evolver_config(db, workspace_id=workspace_id)
    if not evolver_config.enabled:
        raise EvolverDisabledError(
            "Workspace has the evolver disabled; admin must opt in via /admin/settings/evolver."
        )

    breaker_open = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=int(evolver_config.evolver_breaker_strikes),
    )
    if breaker_open:
        raise EvolverBreakerOpenError(
            "Evolver breaker is open; back off and retry after the cooldown."
        )

    aux_config = await _resolve_aux_config(workspace_id=workspace_id, evolver_config=evolver_config)
    if aux_config is None:
        raise EvolverAuxModelMissingError(
            "No aux model resolved for the evolver; configure aux_model_evolver "
            "or enable a workspace chat provider."
        )
    model = _build_pydantic_ai_model_from_config(aux_config)
    if model is None:
        raise EvolverAuxModelMissingError(
            f"Failed to instantiate pydantic-ai model {aux_config.model!r}; "
            "check the workspace provider config."
        )

    aux_model_name = aux_config.model

    triggering_list = list(triggering_run_ids or [])
    await _record_audit(
        workspace_id=workspace_id,
        actor_identity_id=actor_identity_id,
        action=AUDIT_INVOKED,
        summary=(
            f"evolver subagent invoked ({invocation_kind})"
            f" for {len(triggering_list)} triggering run(s)"
        ),
        metadata={
            "run_id": str(run_id),
            "invocation_kind": invocation_kind,
            "triggering_run_ids": [str(r) for r in triggering_list],
            "timeout_seconds": int(timeout),
            "aux_model": aux_model_name,
        },
    )

    # ToolRunContext is the read side every builtin tool uses; the
    # evolver runs *outside* a chat session so session_id / agent_id
    # carry sentinel UUIDs derived from the workspace + run id. The
    # tool implementations only consult workspace_id / run_id /
    # identity_id for audit + scoping, so the synthetic ids are safe.
    sentinel_session_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"evolver:session:{run_id}")
    sentinel_agent_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"evolver:agent:{workspace_id}")
    tool_ctx = ToolRunContext(
        run_id=run_id,
        workspace_id=workspace_id,
        session_id=sentinel_session_id,
        identity_id=actor_identity_id or sentinel_agent_id,
        agent_id=sentinel_agent_id,
        scratch_base=Path("."),
        policy={
            "agent_kind": EVOLVER_AGENT_KIND,
            "workspace_id": str(workspace_id),
            "invocation_kind": invocation_kind,
        },
    )

    agent = build_evolver_agent(model=model)
    user_prompt = _initial_user_prompt(
        triggering_run_ids=triggering_list,
        invocation_kind=invocation_kind,
    )

    final_message: str | None = None
    error_message: str | None = None
    timed_out = False
    skipped = False

    set_context(tool_ctx)
    try:
        try:
            result = await asyncio.wait_for(agent.run(user_prompt), timeout=timeout)
        except TimeoutError:
            timed_out = True
            error_message = f"evolver run exceeded {timeout}s wall clock and was aborted."
            log.warning(
                "evolver subagent timeout workspace=%s run=%s timeout=%ss",
                workspace_id,
                run_id,
                timeout,
            )
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            log.exception(
                "evolver subagent failed workspace=%s run=%s",
                workspace_id,
                run_id,
            )
        else:
            output = getattr(result, "output", None)
            if output is None:
                output = getattr(result, "data", None)
            final_message = (
                output if isinstance(output, str) else (str(output) if output is not None else None)
            )
            if final_message and "no SkillPack proposals" in final_message.lower():
                skipped = True
    finally:
        set_context(None)

    proposals_created = await _count_proposals_for_run(workspace_id=workspace_id, run_id=run_id)

    # mark_skip writes its own ``evolver.marked_skip`` audit row, so we
    # only need to detect "skipped" here for the result struct.
    if not skipped and proposals_created == 0 and not error_message:
        # Heuristic: when the agent produced zero proposals and didn't
        # hit an error path, classify as a skip even if the model
        # forgot to call mark_skip — keeps the dashboard honest.
        skipped = True

    duration_ms = int((time.perf_counter() - started) * 1000)

    if timed_out:
        await _bump_breaker(workspace_id=workspace_id, evolver_config=evolver_config)
        await _record_audit(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=AUDIT_TIMEOUT,
            summary=f"evolver subagent timed out after {timeout}s",
            metadata={
                "run_id": str(run_id),
                "timeout_seconds": int(timeout),
                "duration_ms": duration_ms,
                "proposals_created": proposals_created,
                "aux_model": aux_model_name,
            },
        )
    elif error_message is not None:
        await _bump_breaker(workspace_id=workspace_id, evolver_config=evolver_config)
        await _record_audit(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=AUDIT_FAILED,
            summary="evolver subagent failed",
            metadata={
                "run_id": str(run_id),
                "duration_ms": duration_ms,
                "proposals_created": proposals_created,
                "aux_model": aux_model_name,
                "error": error_message,
            },
        )
    else:
        await _record_audit(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=AUDIT_SUBAGENT_COMPLETED,
            summary=(
                f"evolver subagent completed (proposals={proposals_created}, skipped={skipped})"
            ),
            metadata={
                "run_id": str(run_id),
                "duration_ms": duration_ms,
                "proposals_created": proposals_created,
                "skipped": skipped,
                "aux_model": aux_model_name,
                "invocation_kind": invocation_kind,
            },
        )

    return EvolverInvokeResult(
        run_id=run_id,
        proposals_created=proposals_created,
        skipped=skipped,
        duration_ms=duration_ms,
        final_message=final_message,
        error=error_message,
        aux_model=aux_model_name,
        timed_out=timed_out,
    )
