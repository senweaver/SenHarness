"""Pydantic-AI backend runner — real Agent.run_stream integration.

Emits normalized `RunEvent` frames so the WebSocket layer can forward them as-is
to the frontend. Falls back to a deterministic echo stream when no model provider
is configured (so dev onboarding doesn't stall on API keys).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

from app.agents.harness.coding import (
    build_coding_prompt_fragment,
    prefer_filesystem_sandbox_capability,
)
from app.agents.harness.context import build_history_processors
from app.agents.harness.memory import fetch_system_memory_fragment
from app.agents.harness.planner import merge_planner_into_subagents
from app.agents.harness.plugin_host import plugin_host
from app.agents.harness.reliability import (
    StuckLoopAbort,
    pick_reasoning_effort,
    repair_orphan_tool_calls,
    truncate_tool_output,
)
from app.agents.harness.reliability import (
    build_state as build_reliability_state,
)
from app.agents.harness.sandbox import (
    SandboxMisconfiguredError,
    build_sandbox,
    register_active_backend,
    unregister_active_backend,
)
from app.agents.harness.shields import (
    build_content_guards,
    build_cost_tracking,
    build_tool_guard,
)
from app.agents.harness.skills import build_skills_capability
from app.agents.harness.subagents import build_subagent_capability
from app.agents.harness.todos import build_todo_capability
from app.agents.kernels.base import (
    AgentBackend,
    BackendCapabilities,
    RunEvent,
    RunEventKind,
    RunRequest,
)
from app.agents.kernels.deps import SenHarnessDeps
from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
    resolve_for_agent,
)
from app.agents.kernels.native._cache_wiring import (
    CacheWiringResult,
    finalize as cache_finalize,
    prepare as cache_prepare,
)
from app.agents.kernels.native._failover import (
    AllProvidersUnavailable,
    ProviderFailoverHint,
    run_with_failover,
)
from app.agents.kernels.native._reflection import (
    audit_reflection,
    build_reflection_config,
    inject_ephemeral_system_message,
)
from app.agents.kernels.native.capabilities import CAPABILITIES
from app.agents.tools import BUILTIN_TOOL_REGISTRY, DEFAULT_TOOLBOX, BuiltinTool
from app.agents.tools._context import ToolRunContext, set_context
from app.core.config import settings
from app.core.pricing import calc_cost_usd
from app.core.prometheus import record_run, record_tool_call
from app.db.session import get_session_factory
from app.db.models.agent import Agent as _AgentModel
from app.services import audit as audit_svc
from app.services.provider_chain import (
    get_provider_chain,
    get_workspace_failover_config,
)
from app.services.served_model import resolve_served_model

log = logging.getLogger(__name__)


class _ServedEnvelope:
    """Pre-resolution envelope: held by ``_run_inner`` between the
    served lookup and the upstream resolver call.
    """

    __slots__ = ("served_name", "upstream", "matched_via", "applied_override")

    def __init__(
        self,
        *,
        served_name: str,
        upstream: str,
        matched_via: str,
        applied_override: str | None,
    ) -> None:
        self.served_name = served_name
        self.upstream = upstream
        self.matched_via = matched_via
        self.applied_override = applied_override


async def _resolve_served_envelope(req: RunRequest) -> _ServedEnvelope:
    """Open a short-lived DB session, look up the agent's served name
    + workspace alias map, and return what the runner needs.

    A failure here logs and falls back to the no-rename path so a
    transient DB hiccup at run start can never break the chat turn.
    The runner then proceeds with ``req.model_override`` unchanged.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            agent_orm = await db.get(_AgentModel, req.agent_id)
            resolved = await resolve_served_model(
                db,
                workspace_id=req.workspace_id,
                agent=agent_orm,
                fallback_upstream=(req.policy or {}).get("model")
                if isinstance(req.policy, dict)
                else None,
            )
    except Exception:  # pragma: no cover — defensive
        log.warning("served_model resolution failed run=%s", req.run_id, exc_info=True)
        return _ServedEnvelope(
            served_name="",
            upstream="",
            matched_via="fallback",
            applied_override=None,
        )

    # Only inject an override when the alias map redirects upstream
    # AND no per-turn override was already supplied by the composer.
    applied_override: str | None = None
    if (
        resolved.matched_via == "workspace_alias"
        and resolved.upstream
        and not (req.model_override and req.model_override.strip())
    ):
        applied_override = resolved.upstream

    return _ServedEnvelope(
        served_name=resolved.served_name,
        upstream=resolved.upstream,
        matched_via=resolved.matched_via,
        applied_override=applied_override,
    )


async def _audit_upstream_called(
    *,
    req: RunRequest,
    served_name: str,
    upstream: str,
    provider_kind: str,
) -> None:
    """Diagnostic-only audit: ``provider.upstream_called`` records the
    real upstream so operators can debug provider routing without
    every audit row leaking the upstream id.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="provider.upstream_called",
                actor_identity_id=req.identity_id,
                workspace_id=req.workspace_id,
                resource_type="agent",
                resource_id=req.agent_id,
                summary=f"{served_name} → {upstream} via workspace_alias",
                metadata={
                    "run_id": str(req.run_id),
                    "served_model_name": served_name,
                    "upstream_model": upstream,
                    "provider_kind": provider_kind,
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover — defensive
        log.warning(
            "provider.upstream_called audit failed run=%s", req.run_id, exc_info=True
        )


class NativeBackend(AgentBackend):
    backend_kind = "native"

    # Per-run task tracking so the WS handler can request a soft cancel via
    # ``cancel(run_id)``. Keys are ``RunRequest.run_id`` UUIDs; values are the
    # ``asyncio.Task`` running the agent loop. We register on entry and
    # unregister in a ``finally`` so a race between user cancel and natural
    # completion never leaves a stale entry around.
    _active_runs: ClassVar[dict[uuid.UUID, asyncio.Task]] = {}

    # Per-run telemetry — populated when ``_build_capabilities`` resolves
    # the SkillPacks bound for this run, drained by the M1.5
    # session_artifact + skill_usage capture path. Defensive cleanup
    # ensures a long-running process never leaks pack id lists from
    # finished runs.
    _injected_skill_ids: ClassVar[dict[uuid.UUID, list[uuid.UUID]]] = {}

    def capabilities(self) -> BackendCapabilities:
        return CAPABILITIES

    @classmethod
    def get_injected_skill_ids(cls, run_id: uuid.UUID) -> list[uuid.UUID]:
        """Pack ids resolved at ``_build_capabilities`` time for ``run_id``.

        Returns ``[]`` for runs that did not bind any packs (or whose
        injected list was already cleared after finalize). Always safe
        for capture-time callers to read without locking — the dict is
        only mutated from inside the run task itself.
        """
        return list(cls._injected_skill_ids.get(run_id, []))

    @classmethod
    def _clear_injected_skill_ids(cls, run_id: uuid.UUID) -> None:
        cls._injected_skill_ids.pop(run_id, None)

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        # Bind the *current* asyncio task as the run owner. The WS handler
        # already wraps ``backend.run(...)`` consumption in its own task, so
        # ``current_task()`` is the right handle to cancel.
        current = asyncio.current_task()
        if current is not None:
            NativeBackend._active_runs[req.run_id] = current
        first_delta_fired = False

        from app.services.agent_runtime import (
            publish_run_card_update,
            write_phase,
        )

        async def _on_runtime_phase(
            phase: str | None,
            *,
            tool: str | None = "__keep__",
        ) -> None:
            try:
                await write_phase(
                    run_id=req.run_id,
                    phase=phase,
                    running_tool_name=tool,
                )
            except Exception:
                log.exception(
                    "agent_runtime write_phase failed run=%s", req.run_id
                )
            try:
                publish_run_card_update(
                    workspace_id=req.workspace_id,
                    run_id=req.run_id,
                    session_id=req.session_id,
                    payload={
                        "current_phase": phase,
                        "running_tool_name": None if tool == "__keep__" else tool,
                    },
                )
            except Exception:
                log.exception(
                    "agent_runtime publish failed run=%s", req.run_id
                )

        await _on_runtime_phase("planning", tool=None)

        try:
            async for ev in self._run_inner(req):
                if (
                    not first_delta_fired
                    and ev.kind == RunEventKind.DELTA
                    and req.on_first_delta is not None
                ):
                    first_delta_fired = True
                    try:
                        req.on_first_delta()
                    except Exception:
                        log.exception("on_first_delta callback raised")

                # Live phase + tool probes. We deliberately keep these
                # off the hot text-delta path so the DB doesn't get
                # spammed with one UPDATE per token.
                if ev.kind == RunEventKind.TOOL_CALL:
                    tool_name = str(ev.data.get("name") or "") or None
                    await _on_runtime_phase(
                        "executing_tool", tool=tool_name
                    )
                elif ev.kind == RunEventKind.TOOL_RESULT:
                    await _on_runtime_phase("planning", tool=None)
                elif ev.kind == RunEventKind.FINAL:
                    await _on_runtime_phase(None, tool=None)
                yield ev
        finally:
            NativeBackend._active_runs.pop(req.run_id, None)
            NativeBackend._clear_injected_skill_ids(req.run_id)
            try:
                await write_phase(
                    run_id=req.run_id, phase=None, running_tool_name=None
                )
            except Exception:
                log.exception(
                    "agent_runtime cleanup write_phase failed run=%s",
                    req.run_id,
                )

    async def _run_inner(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        # Two-Model-ID resolution (M2.5.7) — happens BEFORE provider
        # resolution so that an alias map entry can redirect the
        # upstream while ``served_model_name`` (what clients/audit
        # see) stays stable. The actual LLM call uses ``upstream``.
        served_envelope = await _resolve_served_envelope(req)

        resolved = await resolve_for_agent(
            workspace_id=req.workspace_id,
            agent_id=req.agent_id,
            override=served_envelope.applied_override or req.model_override,
        )

        if resolved is None:
            async for ev in _placeholder_stream(req, reason="no_model_configured"):
                yield ev
            return

        # Stash the served name on policy + a private slot on the
        # request object so ``_pydantic_ai_stream`` can lift it onto
        # the per-run context without re-querying the DB.
        served_name = served_envelope.served_name or resolved.model_name
        if isinstance(req.policy, dict):
            req.policy["served_model_name"] = served_name

        if served_envelope.matched_via == "workspace_alias":
            await _audit_upstream_called(
                req=req,
                served_name=served_name,
                upstream=resolved.model_name,
                provider_kind=resolved.provider_kind,
            )

        log.info(
            "model build attempt provider=%s model=%s served=%s "
            "matched_via=%s source=%s base_url=%s has_key=%s",
            resolved.provider_kind,
            resolved.model_name,
            served_name,
            served_envelope.matched_via,
            resolved.source,
            resolved.base_url,
            bool(resolved.api_key),
        )
        model = build_pydantic_ai_model(resolved)
        if model is not None:
            log.info(
                "model build success provider=%s model=%s served=%s source=%s",
                resolved.provider_kind,
                resolved.model_name,
                served_name,
                resolved.source,
            )
            async for ev in _stream_with_optional_failover(
                req,
                primary_model=model,
                primary_resolved=resolved,
                served_name=served_name,
            ):
                yield ev
            return

        log.warning(
            "model build failed provider=%s model=%s source=%s",
            resolved.provider_kind,
            resolved.model_name,
            resolved.source,
        )

        # Per-agent fallback (``metadata.fallback_model``): when the
        # primary provider is configured but its model can't be built
        # (key revoked, provider library missing, etc.) try the agent's
        # declared alternative before surrendering to the placeholder.
        fallback_str: Any = None
        if isinstance(req.policy, dict):
            fallback_str = req.policy.get("fallback_model")
        if isinstance(fallback_str, str) and ":" in fallback_str:
            fb_resolved = await resolve_for_agent(
                workspace_id=req.workspace_id,
                agent_id=req.agent_id,
                override=fallback_str,
            )
            if fb_resolved is not None:
                fb_model = build_pydantic_ai_model(fb_resolved)
                if fb_model is not None:
                    log.info(
                        "fallback model build success provider=%s model=%s",
                        fb_resolved.provider_kind,
                        fb_resolved.model_name,
                    )
                    async for ev in _stream_with_optional_failover(
                        req,
                        primary_model=fb_model,
                        primary_resolved=fb_resolved,
                        served_name=served_name,
                    ):
                        yield ev
                    return

        async for ev in _placeholder_stream(
            req, reason=f"model_build_failed:{resolved.provider_kind}"
        ):
            yield ev

    async def cancel(self, run_id: uuid.UUID) -> None:
        """Request a cooperative cancel of an in-flight run.

        Returns immediately after issuing ``Task.cancel()``; the actual
        teardown (closing pydantic-ai's ``agent.iter`` context, unregistering
        sandboxes etc.) happens in the run's own ``finally`` blocks.
        """
        task = NativeBackend._active_runs.get(run_id)
        if task is None or task.done():
            return None
        task.cancel()
        return None


async def _placeholder_hint(req: RunRequest) -> str:
    """Return a workspace-aware hint explaining why no model resolved.

    Three branches:
      - workspace has zero ModelProvider rows → tell the user to add one
      - workspace has rows but all disabled / missing keys → tell them
        which provider to flip on
      - lookup failed for any other reason → generic guidance
    """
    try:
        from sqlalchemy import select

        from app.db.models.model_provider import ModelKey, ModelProvider
        from app.db.session import get_session_factory
    except ImportError:
        return "请在 设置 → 模型供应商 中新增一个供应商并填入 API Key。"

    factory = get_session_factory()
    try:
        async with factory() as session:
            stmt = (
                select(ModelProvider.kind, ModelProvider.name, ModelProvider.enabled, ModelKey.enabled)
                .join(ModelKey, ModelKey.provider_id == ModelProvider.id, isouter=True)
                .where(
                    ModelProvider.workspace_id == req.workspace_id,
                    ModelProvider.deleted_at.is_(None),
                )
                .order_by(ModelProvider.created_at.asc())
                .limit(20)
            )
            rows = (await session.execute(stmt)).all()
    except Exception:  # pragma: no cover — defensive
        log.warning("placeholder hint lookup failed run=%s", req.run_id, exc_info=True)
        return "请在 设置 → 模型供应商 中新增一个供应商并填入 API Key。"

    if not rows:
        return (
            "请打开 设置 → 模型供应商，新增一个供应商（如 DeepSeek / OpenAI / "  # noqa: RUF001
            "Anthropic / OpenRouter / Ollama 等）并填入 API Key。"  # noqa: RUF001
        )

    disabled = [(k, n) for k, n, prov_enabled, _key_enabled in rows if not prov_enabled]
    no_key = [
        (k, n)
        for k, n, prov_enabled, key_enabled in rows
        if prov_enabled and not key_enabled
    ]
    if disabled:
        kind, name = disabled[0]
        return (
            f"已检测到禁用的供应商「{name}」（{kind}）。"  # noqa: RUF001
            f"请到 设置 → 模型供应商 将其重新启用，或新增并启用其它供应商。"  # noqa: RUF001
        )
    if no_key:
        kind, name = no_key[0]
        return (
            f"供应商「{name}」（{kind}）已启用，但未配置可用的 API Key。"  # noqa: RUF001
            f"请到 设置 → 模型供应商 为它填入 / 启用一个 Key。"
        )
    return "请到 设置 → 模型供应商 检查供应商状态与 API Key 配置。"


# ─── Placeholder stream (no model configured) ─────────────────
async def _placeholder_stream(req: RunRequest, *, reason: str) -> AsyncIterator[RunEvent]:
    hint = await _placeholder_hint(req)
    preamble = (
        f"[SenHarness · {reason}] "
        f"当前工作区没有可用的模型供应商。"
        f"{hint}"
        f"你的消息："  # noqa: RUF001
    )
    full = preamble + req.user_text
    for i in range(0, len(full), 6):
        yield RunEvent(RunEventKind.DELTA, {"text": full[i : i + 6]})
        await asyncio.sleep(0.02)
    yield RunEvent(
        RunEventKind.USAGE,
        {"tokens": {"input": len(req.user_text), "output": len(full)}, "cost": 0.0},
    )
    # ``placeholder=True`` lets the WS handler skip persistence for this
    # synthetic assistant turn. If we let it land in the DB it would re-enter
    # the next turn's ``message_history`` and the model would see a long tail
    # of ``[占位 ...]`` strings, often producing a garbled empty reply that
    # makes the placeholder *appear* permanent across retries.
    yield RunEvent(
        RunEventKind.FINAL,
        {
            "message_id": str(uuid.uuid4()),
            "summary": None,
            "reason": reason,
            "placeholder": True,
        },
    )


# ─── Real pydantic-ai stream ──────────────────────────────────
async def _build_agent(req: RunRequest, *, model: Any, system_prompt: str):
    """Instantiate a pydantic-ai Agent bound to this run."""
    from pydantic_ai import Agent, RunContext

    capabilities, sandbox_backend = await _build_capabilities(req, primary_model=model)
    history_processors = build_history_processors(policy=req.policy, primary_model=model)

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        capabilities=capabilities or None,
        history_processors=history_processors or None,
        deps_type=SenHarnessDeps,
    )
    # Return backend alongside the agent so the deps we pass to `.iter()` can
    # carry it — the ConsoleCapability tools read `ctx.deps.backend`.
    agent._senharness_backend = sandbox_backend  # type: ignore[attr-defined]

    # Register requested tools from the builtin registry. Each tool is a plain
    # function that accepts a validated Pydantic args model and returns a dict.
    # When ConsoleCapability is active (sandbox_backend != None), its toolset
    # already provides read_file/write_file/edit_file/execute/ls/grep/glob with
    # a richer, sandbox-scoped implementation — skip our built-in scratch FS
    # tools to avoid name collisions.
    sandbox_overrides: set[str] = set()
    if sandbox_backend is not None:
        sandbox_overrides = {
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "delete_file",
        }
    # ``policy["agent_kind"]`` is the explicit hint platform-builtin
    # agents (M2.2 evolver) set; absence means "regular workspace
    # agent". Tools that declare ``available_for_kinds`` are skipped
    # silently when the calling agent's kind is not in the allow-list,
    # so the evolver verbs (M2.7) are invisible to non-evolver agents
    # even if their toolbox names them.
    agent_kind = str((req.policy or {}).get("agent_kind") or "").strip().lower() or None
    for name in req.toolbox or _default_toolbox_for_agent():
        if name in sandbox_overrides:
            continue
        tool = BUILTIN_TOOL_REGISTRY.get(name)
        if tool is None:
            continue
        if tool.available_for_kinds is not None and agent_kind not in tool.available_for_kinds:
            log.debug(
                "tool %s not registered: agent_kind=%s not in %s",
                name,
                agent_kind,
                tool.available_for_kinds,
            )
            continue
        _register_tool(agent, tool)

    _ = RunContext  # suppress unused import in case pydantic-ai optimizes imports
    return agent


def _default_toolbox_for_agent() -> list[str]:
    """Default toolbox assigned to every agent unless `RunRequest.toolbox` overrides."""
    return list(DEFAULT_TOOLBOX)


async def _build_capabilities(
    req: RunRequest, *, primary_model: Any
) -> tuple[list[Any], Any | None]:
    """Compose Agent capabilities + any sandbox backend that needs to live on deps.

    Returns ``(capabilities_list, sandbox_backend)``. The backend (if any) must
    be attached to ``SenHarnessDeps.backend`` **before** the agent is invoked,
    otherwise the console tools (read_file/write_file/execute/...) will fail at
    call time because ``ctx.deps.backend`` would be ``None``.

    Policy shape (all keys optional, all on ``req.policy``):

    ``autonomy_level`` (str)
        L1 disables every capability (chat-only). L2/L3 allow tools.

    ``code_mode``         → CodeMode (Monty-sandboxed ``run_code``).
    ``subagents``         → SubAgentCapability for ``task``/``check_task`` etc.
    ``skills``            → SkillsCapability (DB-resolved pack injection).
    ``todos``             → TodoCapability (plan/check lists).
    ``sandbox``           → Full console capability (read_file/write_file/edit/
                           execute/grep/glob/ls) backed by Docker / local / state.

    Side effect: stashes the resolved skill pack id list on
    :data:`NativeBackend._injected_skill_ids` keyed by ``req.run_id``;
    :func:`NativeBackend.get_injected_skill_ids` is the read side and
    :func:`NativeBackend._clear_injected_skill_ids` releases it after the
    run finalises. The list is always written (empty when no packs
    resolve) so the M1.5 capture path can read deterministically without
    a None branch.
    """
    caps: list[Any] = []
    backend: Any = None
    NativeBackend._injected_skill_ids[req.run_id] = []
    policy = merge_planner_into_subagents(req.policy or {})
    autonomy = str(policy.get("autonomy_level", "l2")).lower()
    if autonomy == "l1":
        return caps, None

    code_mode = policy.get("code_mode")
    if code_mode:
        cm = _maybe_code_mode(code_mode)
        if cm is not None:
            caps.append(cm)

    sub = build_subagent_capability(policy=policy, primary_model=primary_model)
    if sub is not None:
        caps.append(sub)

    skills_cap, injected_skill_ids = await _resolve_skills_for_run(
        req=req, policy=policy
    )
    NativeBackend._injected_skill_ids[req.run_id] = injected_skill_ids
    if skills_cap is not None:
        caps.append(skills_cap)

    todos = build_todo_capability(policy=policy)
    if todos is not None:
        caps.append(todos)

    try:
        sandbox_cap, backend = build_sandbox(policy=policy)
    except SandboxMisconfiguredError as e:
        log.warning("sandbox misconfigured: %s (%s)", e.code, e)
        raise
    if sandbox_cap is not None:
        caps.append(sandbox_cap)

    # Optional: when operators opt into ``coding.filesystem_sandbox=true`` and
    # the ``pydantic-ai-filesystem-sandbox`` package is installed, attach its
    # hardened capability in addition to the scratch backend. Agent authors
    # can prefer it via metadata without forcing a runtime swap on everyone.
    coding_block = (policy or {}).get("coding") or {}
    if coding_block.get("filesystem_sandbox"):
        fs_cap = prefer_filesystem_sandbox_capability()
        if fs_cap is not None:
            caps.append(fs_cap)

    # HITL ToolGuard — approves/denies sensitive tools per session.
    # Requires `workspace_id` + `session_id` in policy (set by sessions API).
    try:
        ws_id = uuid.UUID(str(policy.get("workspace_id"))) if policy.get("workspace_id") else None
        sess_id = uuid.UUID(str(policy.get("session_id"))) if policy.get("session_id") else None
    except (ValueError, TypeError):
        ws_id = sess_id = None
    if ws_id and sess_id:
        guard = build_tool_guard(
            policy=policy,
            workspace_id=ws_id,
            session_id=sess_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            requested_by_identity_id=req.identity_id,
        )
        if guard is not None:
            caps.append(guard)

    # Content guards (PII / prompt injection / secret redaction).
    for guard in build_content_guards(policy=policy):
        caps.append(guard)

    # Cost / budget tracking.
    cost = build_cost_tracking(policy=policy, primary_model=primary_model)
    if cost is not None:
        caps.append(cost)

    return caps, backend


async def _resolve_skills_for_run(
    *, req: RunRequest, policy: dict[str, Any]
) -> tuple[Any | None, list[uuid.UUID]]:
    """Open a short-lived DB session, resolve bound packs, and emit one
    ``skill.discovery_resolved`` audit row when any pack was resolved.

    Wrapped end-to-end so a transient DB hiccup at run start cannot
    break the chat turn — the agent simply runs without skills and the
    failure is logged. The audit emission is itself best-effort; a
    failed audit write does not unwind the resolved capability.
    """
    factory = get_session_factory()
    capability: Any | None = None
    injected_ids: list[uuid.UUID] = []
    try:
        async with factory() as db:
            capability, injected_ids = await build_skills_capability(
                policy=policy,
                workspace_id=req.workspace_id,
                db=db,
                run_id=req.run_id,
                session_id=req.session_id,
                agent_id=req.agent_id,
                identity_id=req.identity_id,
            )
            if injected_ids:
                await audit_svc.record(
                    db,
                    action="skill.discovery_resolved",
                    actor_identity_id=req.identity_id,
                    workspace_id=req.workspace_id,
                    resource_type="agent",
                    resource_id=req.agent_id,
                    summary=f"resolved {len(injected_ids)} skill packs for run",
                    metadata={
                        "run_id": str(req.run_id),
                        "workspace_id": str(req.workspace_id),
                        "pack_count": len(injected_ids),
                        "pack_ids": [str(i) for i in injected_ids],
                    },
                )
            # Always commit: M1.8's cap selection may have written
            # ``DROPPED_AT_CAP`` rows + a ``skill.cap_applied`` audit
            # even when zero packs survived; the ``if injected_ids``
            # branch above only handles the discovery audit, not the
            # cap side effects, so we cannot rely on it as the
            # commit anchor anymore.
            await db.commit()
    except Exception:  # pragma: no cover - defensive
        log.exception("skill discovery wrapper failed run=%s", req.run_id)
        return (None, [])
    return (capability, injected_ids)


def _maybe_code_mode(spec: Any) -> Any:
    """Best-effort CodeMode import. `spec` may be ``True``, ``"all"``, or ``list[str]``."""
    try:
        from pydantic_ai_harness import CodeMode
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-harness not installed; CodeMode disabled")
        return None
    try:
        tools_arg: Any = "all"
        if isinstance(spec, list):
            tools_arg = spec
        elif isinstance(spec, str) and spec not in ("all", "true"):
            # Allow comma-separated strings too: "calculator,current_time"
            tools_arg = [t.strip() for t in spec.split(",") if t.strip()]
        return CodeMode(tools=tools_arg)
    except Exception as e:  # pragma: no cover
        log.warning("CodeMode init failed: %s", e)
        return None


def _register_tool(agent, tool: BuiltinTool) -> None:
    """Attach a `BuiltinTool` to a pydantic-ai `Agent`.

    pydantic-ai expects a coroutine function whose parameter types drive the tool
    schema. We build a thin closure around the static runner.
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

    # pydantic-ai 1.x — `@agent.tool_plain` is the non-RunContext decorator.
    agent.tool_plain(_handler)


async def _stream_with_optional_failover(
    req: RunRequest,
    *,
    primary_model: Any,
    primary_resolved: ResolvedModel,
    served_name: str,
) -> AsyncIterator[RunEvent]:
    """Decide between the M2.5.3 chain wrapper and the legacy single
    provider path. ``failover_enabled=False`` is a strict no-op — the
    inner stream is invoked with the same arguments the pre-M2.5.3
    runner used so existing tests + traces stay identical.
    """
    factory = get_session_factory()
    config = None
    chain: list[Any] = []
    try:
        async with factory() as fresh:
            config = await get_workspace_failover_config(
                fresh, workspace_id=req.workspace_id
            )
            if config.enabled:
                chain = await get_provider_chain(
                    fresh,
                    workspace_id=req.workspace_id,
                    served_name=served_name,
                    primary_upstream=(
                        f"{primary_resolved.provider_kind}:"
                        f"{primary_resolved.model_name}"
                    ),
                    config=config,
                )
    except Exception:  # pragma: no cover — degraded DB path
        log.warning(
            "failover config load failed; falling through to single provider",
            exc_info=True,
        )

    if config is None or not config.enabled or not chain:
        async for ev in _pydantic_ai_stream(
            req,
            model=primary_model,
            resolved=primary_resolved,
            served_name=served_name,
        ):
            yield ev
        return

    redis = _safe_redis_client()
    try:
        async for ev in run_with_failover(
            req,
            primary_resolved=primary_resolved,
            primary_model=primary_model,
            served_name=served_name,
            chain=chain,
            config=config,
            redis=redis,
            inner_stream=_pydantic_ai_stream,
        ):
            yield ev
    except AllProvidersUnavailable as exhausted:
        # Translate the typed exhaustion into the standard ERROR +
        # FINAL pair the rest of the pipeline expects. The audit
        # ``provider.failover_exhausted`` row was already written by
        # the wrapper; here we only surface the user-visible frames.
        log.warning(
            "provider chain exhausted run=%s attempts=%d",
            req.run_id,
            len(exhausted.attempts),
        )
        yield RunEvent(
            RunEventKind.ERROR,
            {
                "code": "provider.chain_exhausted",
                "message": "all configured providers failed",
                "retryable": False,
                "attempts": exhausted.attempts,
                "served_model": served_name,
            },
        )
        yield RunEvent(
            RunEventKind.FINAL,
            {
                "message_id": str(uuid.uuid4()),
                "summary": None,
                "reason": "provider_chain_exhausted",
                "served_model": served_name,
            },
        )


def _safe_redis_client() -> Any | None:
    """Best-effort Redis client lookup for the chain wrapper."""
    try:
        from app.core.rate_limit import get_redis

        return get_redis()
    except Exception:  # pragma: no cover — degraded path
        return None


def resolve_reasoning_effort_for_run(
    *, policy: dict[str, Any] | None, user_text: str
) -> str | None:
    explicit_effort = (policy or {}).get("reasoning_effort")
    mode_policy = str((policy or {}).get("mode") or "").strip().lower()
    if isinstance(explicit_effort, str) and explicit_effort.strip():
        return explicit_effort.strip().lower()
    if mode_policy == "flash":
        return None
    return pick_reasoning_effort(user_text, policy=policy or {})


async def _pydantic_ai_stream(
    req: RunRequest,
    *,
    model: Any,
    resolved: ResolvedModel,
    served_name: str | None = None,
    raise_provider_errors: bool = False,
) -> AsyncIterator[RunEvent]:
    """Drive a pydantic-ai Agent and emit SenHarness RunEvent frames.

    ``served_name`` (M2.5.7) is the stable client-facing name; when
    provided it lands on every ``USAGE`` / ``FINAL`` event so
    downstream persistence can populate ``token_usage_json.model``
    with the brand name instead of the upstream id. When ``None``
    the resolver's upstream model name is used as both, preserving
    behaviour for callers that don't know about the pattern yet.

    ``raise_provider_errors`` is set by the M2.5.3 chain wrapper:
    when a retryable provider exception fires *before* any visible
    output has been streamed, the wrapper wants the exception
    (typed as :class:`ProviderFailoverHint`) instead of an ``ERROR``
    + ``FINAL`` pair so it can quietly retry on the next chain entry.
    Default ``False`` preserves the legacy "always emit terminal
    frames" contract for the single-provider path.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartDeltaEvent,
        PartStartEvent,
        TextPartDelta,
        ThinkingPartDelta,
    )

    # Install per-run context so filesystem tools etc. can resolve the scratch dir.
    scratch_base = Path(settings.STORAGE_LOCAL_PATH) / "scratch"
    tool_ctx = ToolRunContext(
        run_id=req.run_id,
        workspace_id=req.workspace_id,
        session_id=req.session_id,
        identity_id=req.identity_id,
        agent_id=req.agent_id,
        scratch_base=scratch_base,
        policy=req.policy or {},
    )
    set_context(tool_ctx)

    # M2.5.5 plugin host: announce the chat-turn boundary so plugins
    # can attach per-session state. ``fire`` is fail-safe (per-callback
    # timeout + audit on error) so no defensive try/except is needed
    # at this call site — a misbehaving plugin can't break the run.
    await plugin_host.fire(
        "on_session_start",
        run_id=req.run_id,
        workspace_id=req.workspace_id,
        session_id=req.session_id,
        identity_id=req.identity_id,
        agent_id=req.agent_id,
        served_model=served_name,
        upstream_model=resolved.model_name,
        provider_kind=resolved.provider_kind,
    )
    final_outcome: str = "completed"

    async def _fire_session_end(outcome: str) -> None:
        # Tiny inline helper so every exit branch can close the
        # session without copy-pasting the same kwargs. fire() never
        # raises (timeout + audit on error), so this is safe to call
        # even from inside an exception handler.
        await plugin_host.fire(
            "on_session_end",
            run_id=req.run_id,
            workspace_id=req.workspace_id,
            session_id=req.session_id,
            identity_id=req.identity_id,
            agent_id=req.agent_id,
            served_model=served_name,
            upstream_model=resolved.model_name,
            provider_kind=resolved.provider_kind,
            final_outcome=outcome,
        )

    # Auto-recall relevant memories and fold them into the system prompt.
    memory_fragment = await fetch_system_memory_fragment(
        workspace_id=req.workspace_id,
        identity_id=req.identity_id,
        agent_id=req.agent_id,
        user_text=req.user_text,
    )

    agent = await _build_agent(
        req,
        model=model,
        system_prompt=_assemble_prompt(req, memory_fragment=memory_fragment, resolved=resolved),
    )

    # Reliability state governs stuck-loop detection, tool retry budgets,
    # limit warnings, tool_output overflow and adaptive reasoning. The
    # reflection block (M0.4 / M0.5) is resolved up-front so a workspace-level
    # disable short-circuits the per-iteration check without any DB hit.
    reflection_config = await build_reflection_config(
        workspace_id=req.workspace_id,
        agent_policy=req.policy or {},
    )
    reliability = build_reliability_state(
        policy=req.policy or {},
        max_iterations=req.iteration_budget,
        reflection_config=reflection_config,
    )

    effort = resolve_reasoning_effort_for_run(policy=req.policy, user_text=req.user_text)
    if effort is not None:
        try:
            ms = getattr(agent, "model_settings", None)
            if ms is None:
                # Some pydantic-ai versions lazily allocate ``model_settings``;
                # create a fresh dict so the flag actually lands.
                agent.model_settings = {"reasoning_effort": effort}  # type: ignore[attr-defined]
            else:
                ms["reasoning_effort"] = effort  # type: ignore[index]
        except Exception:  # pragma: no cover
            log.debug("could not apply reasoning_effort=%s", effort)

    qwen3_extra = _qwen3_extra_body(req.policy, resolved)
    if qwen3_extra is not None:
        try:
            ms = getattr(agent, "model_settings", None)
            if ms is None:
                agent.model_settings = {"extra_body": dict(qwen3_extra)}  # type: ignore[attr-defined]
            else:
                existing = ms.get("extra_body") or {}  # type: ignore[union-attr]
                ms["extra_body"] = {**existing, **qwen3_extra}  # type: ignore[index]
        except Exception:  # pragma: no cover
            log.debug("could not apply qwen3 extra_body=%s", qwen3_extra)

    deepseek_extra = _deepseek_extra_body(req.policy, resolved)
    if deepseek_extra is not None:
        try:
            ms = getattr(agent, "model_settings", None)
            if ms is None:
                agent.model_settings = {"extra_body": dict(deepseek_extra)}  # type: ignore[attr-defined]
            else:
                existing = ms.get("extra_body") or {}  # type: ignore[union-attr]
                ms["extra_body"] = {**existing, **deepseek_extra}  # type: ignore[index]
        except Exception:  # pragma: no cover
            log.debug("could not apply deepseek extra_body=%s", deepseek_extra)

    history = _rehydrate_history(req.message_history)
    history = repair_orphan_tool_calls(history)

    # M2.5.9 — provider cache marker wiring. Resolves workspace +
    # platform settings, checks the adaptive disable window, and
    # applies provider-native cache settings on ``agent.model_settings``
    # for supported providers (Anthropic native; OpenRouter intent
    # only). Falls through to no-op on any error so an unconfigured
    # workspace or a degraded settings layer can't break the turn.
    cache_redis = _safe_redis_client()
    cache_wiring: CacheWiringResult = await cache_prepare(
        agent=agent,
        workspace_id=req.workspace_id,
        provider_kind=resolved.provider_kind,
        redis=cache_redis,
    )

    # Scratch directory for tool-output overflow dumps.
    overflow_dir = (
        Path(settings.STORAGE_LOCAL_PATH) / "scratch" / str(req.session_id)
    )

    started = time.perf_counter()
    final_text = ""
    final_message_id = str(uuid.uuid4())
    usage_total = {"input": 0, "output": 0}

    sandbox_backend = getattr(agent, "_senharness_backend", None)
    deps = SenHarnessDeps(
        run_id=req.run_id,
        workspace_id=req.workspace_id,
        session_id=req.session_id,
        identity_id=req.identity_id,
        agent_id=req.agent_id,
        backend=sandbox_backend,
    )
    if sandbox_backend is not None:
        register_active_backend(req.run_id, sandbox_backend)

    # Build the prompt — either a plain string (text-only) or a list with
    # pydantic-ai ``BinaryContent`` parts when the user attached images.
    prompt_input: Any = req.user_text
    image_attachments = [
        a for a in (req.attachments or []) if a.get("kind") == "image"
    ]
    if image_attachments:
        try:
            from pydantic_ai import BinaryContent

            parts: list[Any] = []
            if req.user_text:
                parts.append(req.user_text)
            for a in image_attachments:
                data = a.get("data")
                mime = a.get("mime_type") or "image/png"
                if isinstance(data, bytes) and data:
                    parts.append(BinaryContent(data=data, media_type=mime))
            if parts:
                prompt_input = parts
        except Exception as e:  # pragma: no cover
            log.warning("BinaryContent unavailable; dropping images: %s", e)

    try:
        async with agent.iter(
            prompt_input, message_history=history, deps=deps
        ) as agent_run:
            async for node in agent_run:
                # Each graph node roughly corresponds to one reasoning/tool
                # iteration from the user's perspective; tick the budget and
                # emit a warning once we cross the configured ratio.
                reliability.tick_iteration()
                warning = reliability.limit_warning()
                if warning:
                    yield RunEvent(RunEventKind.THINKING, {"text": warning})

                if Agent.is_model_request_node(node):
                    # Reflection injection happens *before* this node streams
                    # so the SystemPromptPart lands in the same model call
                    # the agent is about to make. The mutation is scoped to
                    # ``node.request.parts`` (consumed by ``_prepare_request``
                    # for this iter only) — DB-persisted message rows and
                    # next-turn ``_rehydrate_history`` are untouched, so
                    # provider-side prompt-cache prefix stays stable.
                    decision = reliability.should_reflect()
                    if (
                        decision.should_inject
                        and decision.rendered_prompt
                        and inject_ephemeral_system_message(
                            node, decision.rendered_prompt
                        )
                    ):
                        await audit_reflection(
                            workspace_id=req.workspace_id,
                            actor_identity_id=req.identity_id,
                            run_id=req.run_id,
                            session_id=req.session_id,
                            kind=decision.kind,  # type: ignore[arg-type]
                            iteration=reliability.iteration_count,
                            tool_call_count=reliability.tool_call_count,
                            prompt_chars=len(decision.rendered_prompt),
                            truncated=decision.truncated,
                        )
                    # M2.5.5: pre-LLM hook fires once per upstream model
                    # request (one per ``is_model_request_node``). The
                    # payload carries served + upstream identifiers so a
                    # plugin can route on either the brand name or the
                    # resolved provider id.
                    await plugin_host.fire(
                        "pre_llm_call",
                        run_id=req.run_id,
                        workspace_id=req.workspace_id,
                        session_id=req.session_id,
                        agent_id=req.agent_id,
                        iteration=reliability.iteration_count,
                        served_model=served_name,
                        upstream_model=resolved.model_name,
                        provider_kind=resolved.provider_kind,
                    )
                    llm_text_chars = 0
                    async with node.stream(agent_run.ctx) as response_stream:
                        async for event in response_stream:
                            if isinstance(event, PartStartEvent):
                                continue
                            if isinstance(event, PartDeltaEvent):
                                if isinstance(event.delta, TextPartDelta):
                                    chunk = event.delta.content_delta or ""
                                    if chunk:
                                        final_text += chunk
                                        llm_text_chars += len(chunk)
                                        yield RunEvent(
                                            RunEventKind.DELTA, {"text": chunk}
                                        )
                                elif isinstance(event.delta, ThinkingPartDelta):
                                    chunk = event.delta.content_delta or ""
                                    if chunk:
                                        yield RunEvent(
                                            RunEventKind.THINKING, {"text": chunk}
                                        )
                    await plugin_host.fire(
                        "post_llm_call",
                        run_id=req.run_id,
                        workspace_id=req.workspace_id,
                        session_id=req.session_id,
                        agent_id=req.agent_id,
                        iteration=reliability.iteration_count,
                        served_model=served_name,
                        upstream_model=resolved.model_name,
                        provider_kind=resolved.provider_kind,
                        text_chars=llm_text_chars,
                    )
                elif Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                args = _safe_args(event.part)
                                reliability.record_tool_call(
                                    event.part.tool_name, args
                                )
                                # M0.5: per-call tick — `is_call_tools_node`
                                # may carry multiple parallel calls in one
                                # node, so we count each one rather than the
                                # node itself.
                                reliability.tick_tool_call()
                                record_tool_call(event.part.tool_name)
                                stuck, repeated = reliability.is_stuck()
                                if stuck:
                                    yield RunEvent(
                                        RunEventKind.THINKING,
                                        {
                                            "text": (
                                                f"⚠ 检测到 `{repeated}` 被反复调用，"
                                                f"下一步请换一种策略或给出最终答复。"
                                            )
                                        },
                                    )
                                # If the policy enables hard-abort, this raises
                                # StuckLoopAbort which we surface as a clean
                                # `error` + `final` pair below.
                                reliability.maybe_raise_stuck_loop()
                                # M2.5.5: pre-tool hook fires once per
                                # ``FunctionToolCallEvent`` (parallel calls
                                # arrive as separate events in this loop).
                                await plugin_host.fire(
                                    "pre_tool_call",
                                    run_id=req.run_id,
                                    workspace_id=req.workspace_id,
                                    session_id=req.session_id,
                                    agent_id=req.agent_id,
                                    tool_name=event.part.tool_name,
                                    tool_call_id=(
                                        event.part.tool_call_id
                                        or str(uuid.uuid4())
                                    ),
                                    args=args,
                                )
                                yield RunEvent(
                                    RunEventKind.TOOL_CALL,
                                    {
                                        "id": event.part.tool_call_id or str(uuid.uuid4()),
                                        "name": event.part.tool_name,
                                        "args": args,
                                    },
                                )
                            elif isinstance(event, FunctionToolResultEvent):
                                raw_result = getattr(event.result, "content", None)
                                safe = _json_safe(raw_result)
                                call_id = str(event.tool_call_id or uuid.uuid4())
                                # Result outcome → reflection summary buffer.
                                # We don't have a clean error signal from the
                                # raw event, so treat ``None`` as "no payload"
                                # = error; everything else as ok.
                                tool_name = getattr(
                                    event.result, "tool_name", ""
                                ) or ""
                                reliability.record_tool_outcome(
                                    str(tool_name),
                                    None,
                                    ok=raw_result is not None,
                                )
                                shown, truncated, meta = truncate_tool_output(
                                    safe, policy=req.policy or {}, call_id=call_id
                                )
                                if truncated and meta:
                                    try:
                                        overflow_dir.mkdir(
                                            parents=True, exist_ok=True
                                        )
                                        (
                                            overflow_dir
                                            / f"tool_output_{call_id}.json"
                                        ).write_text(
                                            meta.get("full_payload", ""),
                                            encoding="utf-8",
                                        )
                                    except Exception:  # pragma: no cover
                                        log.warning(
                                            "tool_output overflow dump failed",
                                            exc_info=True,
                                        )
                                # M2.5.5: post-tool hook gets the truncated
                                # payload (same shape downstream consumers
                                # see) plus a deterministic ok/error flag.
                                await plugin_host.fire(
                                    "post_tool_call",
                                    run_id=req.run_id,
                                    workspace_id=req.workspace_id,
                                    session_id=req.session_id,
                                    agent_id=req.agent_id,
                                    tool_name=str(tool_name),
                                    tool_call_id=call_id,
                                    result=shown,
                                    truncated=truncated,
                                    ok=raw_result is not None,
                                )
                                yield RunEvent(
                                    RunEventKind.TOOL_RESULT,
                                    {
                                        "id": call_id,
                                        "result": shown,
                                        "truncated": truncated,
                                    },
                                )
    except StuckLoopAbort as abort:
        # Tool fired ≥ ``stuck_loop_threshold`` times. Surface a deterministic
        # ``stuck_loop`` error + ``final`` so the UI clears the streaming
        # cursor without flashing a scary kernel exception.
        log.warning(
            "stuck_loop abort tool=%s count=%d", abort.tool_name, abort.count
        )
        record_run(
            provider=resolved.provider_kind,
            model=resolved.model_name,
            status="stuck_loop",
            duration_s=(time.perf_counter() - started),
            input_tokens=int(usage_total.get("input") or 0),
            output_tokens=int(usage_total.get("output") or 0),
            cost_usd=0.0,
        )
        yield RunEvent(
            RunEventKind.DELTA,
            {
                "text": (
                    f"\n\n⚠ 检测到工具循环：`{abort.tool_name}` 被连续调用 "  # noqa: RUF001
                    f"{abort.count} 次。已自动终止本轮。"
                )
            },
        )
        yield RunEvent(
            RunEventKind.ERROR,
            {
                "code": "stuck_loop",
                "message": (
                    f"tool {abort.tool_name!r} called {abort.count} times "
                    f"(threshold {abort.threshold})"
                ),
                "retryable": False,
                "tool": abort.tool_name,
            },
        )
        yield RunEvent(
            RunEventKind.FINAL,
            {
                "message_id": final_message_id,
                "summary": None,
                "reason": "stuck_loop",
                "text": final_text,
            },
        )
        set_context(None)
        unregister_active_backend(req.run_id)
        await _fire_session_end("stuck_loop")
        return
    except SandboxMisconfiguredError as e:
        # Operator misconfigured the agent sandbox (e.g. kind=local+execute in
        # prod). Emit the specific reason so the UI doesn't hide the fix.
        log.warning("sandbox misconfigured: %s", e)
        record_run(
            provider=resolved.provider_kind,
            model=resolved.model_name,
            status="sandbox_misconfigured",
            duration_s=(time.perf_counter() - started),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        yield RunEvent(
            RunEventKind.ERROR,
            {
                "code": e.code,
                "message": str(e),
                "retryable": False,
            },
        )
        await _fire_session_end("sandbox_misconfigured")
        return
    except Exception as e:  # pragma: no cover - surfaced to client
        # ToolBlocked from pydantic-ai-shields isn't a bug — it means the user
        # denied / the guard blocked a tool call. Render it as a friendly
        # final message so the UI doesn't flash a scary error.
        try:
            from pydantic_ai_shields.guardrails import (
                GuardrailError,
                ToolBlocked,
            )
            is_guard_block = isinstance(e, (ToolBlocked, GuardrailError))
        except ImportError:  # pragma: no cover
            is_guard_block = False

        if is_guard_block:
            reason = str(e)
            log.info("agent run blocked by guardrail: %s", reason)
            yield RunEvent(
                RunEventKind.DELTA,
                {"text": f"⚠ 本次操作被守卫拦截：{reason}"},
            )
            yield RunEvent(
                RunEventKind.FINAL,
                {
                    "message_id": str(uuid.uuid4()),
                    "summary": None,
                    "reason": "guardrail_blocked",
                },
            )
            await _fire_session_end("guardrail_blocked")
            return

        # Budget exceeded is another non-bug we render gracefully.
        try:
            from pydantic_ai_shields import BudgetExceededError

            if isinstance(e, BudgetExceededError):
                # Emit a structured ``error`` frame *before* the final so
                # the UI can surface a friendly toast (mapped via
                # ``cost.budget_exceeded`` / ``budget_exceeded`` in the
                # frontend's ``friendlyErrorMessage`` helper). The DELTA +
                # FINAL pair keeps the transcript persistable and unlocks
                # the composer just like a normal turn end.
                yield RunEvent(
                    RunEventKind.ERROR,
                    {
                        "code": "cost.budget_exceeded",
                        "message": str(e),
                        "retryable": False,
                        "provider": resolved.provider_kind,
                        "model": resolved.model_name,
                    },
                )
                yield RunEvent(
                    RunEventKind.DELTA,
                    {"text": f"⚠ 预算已用尽：{e}"},
                )
                yield RunEvent(
                    RunEventKind.FINAL,
                    {
                        "message_id": str(uuid.uuid4()),
                        "summary": None,
                        "reason": "budget_exceeded",
                    },
                )
                await _fire_session_end("budget_exceeded")
                return
        except ImportError:  # pragma: no cover
            pass

        # M2.5.3 — when the chain wrapper is driving us and the
        # failure shape is retryable AND nothing visible has reached
        # the client, hand control back to the wrapper as a typed
        # hint. The wrapper will record the failure on the health
        # tracker and try the next chain entry.
        if raise_provider_errors and not final_text:
            from app.services.provider_health import (
                classify_exception,
                is_retryable_failure,
            )

            failure_kind = classify_exception(e)
            if is_retryable_failure(failure_kind):
                set_context(None)
                unregister_active_backend(req.run_id)
                await _fire_session_end("provider_failover_hint")
                raise ProviderFailoverHint(
                    original=e, failure_kind=failure_kind
                ) from e

        log.exception("pydantic-ai run failed")
        yield RunEvent(
            RunEventKind.ERROR,
            {
                "code": "kernel.run_failed",
                "message": str(e),
                "retryable": False,
                "provider": resolved.provider_kind,
                "model": resolved.model_name,
            },
        )
        await _fire_session_end("kernel.run_failed")
        return

    # Extract usage from the completed run, if available.
    raw_usage_obj: Any = None
    try:
        if agent_run.result is not None:
            u = agent_run.result.usage()
            raw_usage_obj = u
            usage_total["input"] = int(getattr(u, "input_tokens", 0) or 0)
            usage_total["output"] = int(getattr(u, "output_tokens", 0) or 0)
    except Exception:  # pragma: no cover
        pass

    # M2.5.9 — record hit/miss against the adaptive tracker, audit the
    # outcome, and surface the cache-hit token count on the USAGE
    # event metadata. ``cache_finalize`` is a no-op when prepare()
    # short-circuited (provider unsupported / workspace disabled /
    # adaptive window active).
    cache_hit_tokens = await cache_finalize(
        result=cache_wiring,
        usage=raw_usage_obj,
        redis=cache_redis,
        actor_identity_id=req.identity_id,
    )

    # Compute dollar cost from model pricing catalog — persisted on the
    # assistant message and aggregated by /metrics/usage.
    cost_info = calc_cost_usd(
        resolved.model_name,
        resolved.provider_kind,
        usage_total.get("input"),
        usage_total.get("output"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    # M2.5.7 — surface ``served_model`` alongside the upstream
    # ``model``. Downstream persistence (sessions / agent_runner)
    # prefers ``served_model`` for ``token_usage_json.model`` so
    # client-visible identifiers stay stable across provider swaps.
    effective_served = served_name or resolved.model_name
    usage_payload_out: dict[str, Any] = {
        "tokens": usage_total,
        "cost": cost_info["cost"],
        "cost_currency": "USD",
        "cost_matched_model": cost_info["matched_model"],
        "latency_ms": latency_ms,
        "provider": resolved.provider_kind,
        "model": effective_served,
        "upstream_model": resolved.model_name,
        "served_model": effective_served,
    }
    if cache_wiring.enabled:
        usage_payload_out["cache"] = {
            "annotated": bool(cache_wiring.annotated),
            "hit_tokens": int(cache_hit_tokens or 0),
            "ttl": cache_wiring.ttl.value,
            "breakpoint_count": int(cache_wiring.breakpoint_count or 0),
        }
    yield RunEvent(RunEventKind.USAGE, usage_payload_out)
    yield RunEvent(
        RunEventKind.FINAL,
        {
            "message_id": final_message_id,
            "summary": None,
            "provider": resolved.provider_kind,
            "model": effective_served,
            "upstream_model": resolved.model_name,
            "served_model": effective_served,
            "text": final_text,
        },
    )

    record_run(
        provider=resolved.provider_kind,
        model=resolved.model_name,
        status="ok",
        duration_s=latency_ms / 1000.0,
        input_tokens=int(usage_total.get("input") or 0),
        output_tokens=int(usage_total.get("output") or 0),
        cost_usd=float(cost_info["cost"] or 0.0),
    )

    set_context(None)
    unregister_active_backend(req.run_id)
    await _fire_session_end(final_outcome)


# ─── Helpers ──────────────────────────────────────────────────
def _qwen3_no_think(policy: dict[str, Any] | None, resolved: "ResolvedModel") -> bool:
    """True when a Qwen3 model should run without the extended reasoning phase.

    Qwen3 models on DashScope honour ``/no_think`` at the end of the system
    prompt as a per-request reasoning toggle.  We disable thinking unless the
    caller explicitly opted into it via ``mode=thinking`` or a non-trivial
    ``reasoning_effort`` override, because the thinking phase can consume 30+
    seconds before emitting any visible text token.
    """
    mode = str((policy or {}).get("mode") or "").strip().lower()
    if mode == "thinking":
        return False
    explicit_effort = (policy or {}).get("reasoning_effort")
    if isinstance(explicit_effort, str) and explicit_effort.strip().lower() in ("medium", "high"):
        return False
    model_flat = resolved.model_name.lower().replace("-", "").replace("_", "").replace(".", "")
    return "qwen3" in model_flat


def _qwen3_extra_body(policy: dict[str, Any] | None, resolved: "ResolvedModel") -> dict[str, Any] | None:
    """Return ``extra_body`` to hard-disable thinking for Qwen3 on DashScope.

    ``/no_think`` in the system prompt only softly reduces the thinking phase;
    the API-level ``enable_thinking=false`` is the reliable way to eliminate it.
    We scope this to Qwen3 models only — GLM-series models return empty
    content when ``enable_thinking`` is set to false.
    """
    if not _qwen3_no_think(policy, resolved):
        return None
    return {"enable_thinking": False}


def _deepseek_no_think(policy: dict[str, Any] | None, resolved: "ResolvedModel") -> bool:
    """True when a DeepSeek hybrid model should skip the thinking phase.

    DeepSeek V4 (``deepseek-v4-pro`` / ``deepseek-v4-flash``) ships hybrid
    chat+reasoning behind one endpoint with thinking ON by default.  We
    flip it off unless the caller opts into reasoning, for two reasons:

    1. Latency — thinking adds 1–10s before the first visible token.
    2. Correctness — once thinking emits ``reasoning_content``, the API
       requires the client to echo it back on every subsequent turn, or
       it 400s with ``reasoning_content ... must be passed back``.  Our
       history pipeline only round-trips visible text, so leaving
       thinking on breaks multi-turn channel conversations.

    Scope is restricted to ``deepseek-v4-*``: the dedicated
    ``deepseek-reasoner`` model is reasoning-only and ``deepseek-chat``
    is already non-thinking — neither honours this toggle.
    """
    mode = str((policy or {}).get("mode") or "").strip().lower()
    if mode == "thinking":
        return False
    explicit_effort = (policy or {}).get("reasoning_effort")
    if isinstance(explicit_effort, str) and explicit_effort.strip().lower() in ("medium", "high"):
        return False
    name = resolved.model_name.lower()
    return name.startswith("deepseek-v4-")


def _deepseek_extra_body(policy: dict[str, Any] | None, resolved: "ResolvedModel") -> dict[str, Any] | None:
    """Return ``extra_body`` to disable thinking on DeepSeek V4 hybrid models."""
    if not _deepseek_no_think(policy, resolved):
        return None
    return {"thinking": {"type": "disabled"}}


def _assemble_prompt(
    req: RunRequest,
    *,
    memory_fragment: str | None = None,
    resolved: "ResolvedModel | None" = None,
) -> str:
    from app.agents.prompts import assemble_system

    persona = (req.policy or {}).get("persona_md") if req.policy else None
    coding_fragment = build_coding_prompt_fragment(req.policy)
    # Fold coding-specific context (repo AGENTS.md / planning protocol /
    # verification hint) into the memory block so the assembler's existing
    # structure is reused verbatim.
    fragment = memory_fragment
    if coding_fragment:
        fragment = coding_fragment if not fragment else f"{coding_fragment}\n\n{fragment}"
    prompt = assemble_system(persona, memory_fragment=fragment)
    if resolved is not None and _qwen3_no_think(req.policy, resolved):
        prompt = prompt + "\n/no_think"
    return prompt


def _rehydrate_history(history: list[dict[str, Any]]) -> list:
    """Convert our stored messages back into pydantic-ai's `ModelMessage` list.

    P1 uses a simple text-only projection: `[ModelRequest[user], ModelResponse[text]]`.
    Tool calls are not replayed (they only matter for the *current* turn).
    """
    if not history:
        return []
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )
    except ImportError:  # pragma: no cover
        return []

    out = []
    for item in history:
        role = item.get("role")
        text = (item.get("content_json") or {}).get("text") or ""
        if not text:
            continue
        if role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        elif role == "assistant":
            out.append(ModelResponse(parts=[TextPart(content=text)]))
    return out


def _safe_args(part: Any) -> dict:
    try:
        return part.args_as_dict()
    except Exception:  # pragma: no cover
        try:
            return dict(part.args or {})
        except Exception:
            return {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    return str(value)
