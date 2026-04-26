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
from typing import Any

from app.agents.harness.coding import (
    build_coding_prompt_fragment,
    prefer_filesystem_sandbox_capability,
)
from app.agents.harness.context import build_history_processors
from app.agents.harness.memory import fetch_system_memory_fragment
from app.agents.harness.reliability import (
    build_state as build_reliability_state,
)
from app.agents.harness.reliability import (
    pick_reasoning_effort,
    repair_orphan_tool_calls,
    truncate_tool_output,
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
from app.agents.kernels.native.capabilities import CAPABILITIES
from app.agents.tools import BUILTIN_TOOL_REGISTRY, DEFAULT_TOOLBOX, BuiltinTool
from app.agents.tools._context import ToolRunContext, set_context
from app.core.config import settings
from app.core.pricing import calc_cost_usd
from app.core.prometheus import record_run, record_tool_call

log = logging.getLogger(__name__)


class NativeBackend(AgentBackend):
    backend_kind = "native"

    def capabilities(self) -> BackendCapabilities:
        return CAPABILITIES

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        resolved = await resolve_for_agent(
            workspace_id=req.workspace_id,
            agent_id=req.agent_id,
            override=req.model_override,
        )

        if resolved is None:
            async for ev in _placeholder_stream(req, reason="no_model_configured"):
                yield ev
            return

        model = build_pydantic_ai_model(resolved)
        if model is None:
            async for ev in _placeholder_stream(
                req, reason=f"model_build_failed:{resolved.provider_kind}"
            ):
                yield ev
            return

        async for ev in _pydantic_ai_stream(req, model=model, resolved=resolved):
            yield ev

    async def cancel(self, run_id: uuid.UUID) -> None:
        # TODO(P2): track per-run task handles in a weakref dict and cancel()
        _ = run_id
        return None


# ─── Placeholder stream (no model configured) ─────────────────
async def _placeholder_stream(req: RunRequest, *, reason: str) -> AsyncIterator[RunEvent]:
    preamble = (
        f"[SenHarness · Phase 1 占位 ({reason})] "
        f"未检测到可用模型。请在 `.env` 配置 "
        f"`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / "
        f"`OPENROUTER_API_KEY` / `OLLAMA_HOST` 等其一。你的消息："
    )
    full = preamble + req.user_text
    for i in range(0, len(full), 6):
        yield RunEvent(RunEventKind.DELTA, {"text": full[i : i + 6]})
        await asyncio.sleep(0.02)
    yield RunEvent(
        RunEventKind.USAGE,
        {"tokens": {"input": len(req.user_text), "output": len(full)}, "cost": 0.0},
    )
    yield RunEvent(
        RunEventKind.FINAL,
        {"message_id": str(uuid.uuid4()), "summary": None, "reason": reason},
    )


# ─── Real pydantic-ai stream ──────────────────────────────────
def _build_agent(req: RunRequest, *, model: Any, system_prompt: str):
    """Instantiate a pydantic-ai Agent bound to this run."""
    from pydantic_ai import Agent, RunContext

    capabilities, sandbox_backend = _build_capabilities(req, primary_model=model)
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
    for name in req.toolbox or _default_toolbox_for_agent():
        if name in sandbox_overrides:
            continue
        tool = BUILTIN_TOOL_REGISTRY.get(name)
        if tool is None:
            continue
        _register_tool(agent, tool)

    _ = RunContext  # suppress unused import in case pydantic-ai optimizes imports
    return agent


def _default_toolbox_for_agent() -> list[str]:
    """Default toolbox assigned to every agent unless `RunRequest.toolbox` overrides."""
    return list(DEFAULT_TOOLBOX)


def _build_capabilities(req: RunRequest, *, primary_model: Any) -> tuple[list[Any], Any | None]:
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
    ``skills``            → SkillsCapability (SKILL.md loading).
    ``todos``             → TodoCapability (plan/check lists).
    ``sandbox``           → Full console capability (read_file/write_file/edit/
                           execute/grep/glob/ls) backed by Docker / local / state.
    """
    caps: list[Any] = []
    backend: Any = None
    policy = req.policy or {}
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

    skills = build_skills_capability(
        policy=policy, workspace_id=policy.get("workspace_id")
    )
    if skills is not None:
        caps.append(skills)

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


async def _pydantic_ai_stream(
    req: RunRequest, *, model: Any, resolved: ResolvedModel
) -> AsyncIterator[RunEvent]:
    """Drive a pydantic-ai Agent and emit SenHarness RunEvent frames."""
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

    # Auto-recall relevant memories and fold them into the system prompt.
    memory_fragment = await fetch_system_memory_fragment(
        workspace_id=req.workspace_id,
        identity_id=req.identity_id,
        agent_id=req.agent_id,
        user_text=req.user_text,
    )

    agent = _build_agent(
        req,
        model=model,
        system_prompt=_assemble_prompt(req, memory_fragment=memory_fragment),
    )

    # Reliability state governs stuck-loop detection, tool retry budgets,
    # limit warnings, tool_output overflow and adaptive reasoning.
    reliability = build_reliability_state(
        policy=req.policy or {}, max_iterations=req.iteration_budget
    )

    # Optionally bump model reasoning_effort for analytical prompts.
    effort = pick_reasoning_effort(req.user_text, policy=req.policy or {})
    if effort is not None:
        try:
            ms = getattr(agent, "model_settings", None)
            if ms is not None:
                ms["reasoning_effort"] = effort  # type: ignore[index]
        except Exception:  # pragma: no cover
            pass

    history = _rehydrate_history(req.message_history)
    history = repair_orphan_tool_calls(history)

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
                    async with node.stream(agent_run.ctx) as response_stream:
                        async for event in response_stream:
                            if isinstance(event, PartStartEvent):
                                continue
                            if isinstance(event, PartDeltaEvent):
                                if isinstance(event.delta, TextPartDelta):
                                    chunk = event.delta.content_delta or ""
                                    if chunk:
                                        final_text += chunk
                                        yield RunEvent(
                                            RunEventKind.DELTA, {"text": chunk}
                                        )
                                elif isinstance(event.delta, ThinkingPartDelta):
                                    chunk = event.delta.content_delta or ""
                                    if chunk:
                                        yield RunEvent(
                                            RunEventKind.THINKING, {"text": chunk}
                                        )
                elif Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                args = _safe_args(event.part)
                                reliability.record_tool_call(
                                    event.part.tool_name, args
                                )
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
                                yield RunEvent(
                                    RunEventKind.TOOL_RESULT,
                                    {
                                        "id": call_id,
                                        "result": shown,
                                        "truncated": truncated,
                                    },
                                )
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
            return

        # Budget exceeded is another non-bug we render gracefully.
        try:
            from pydantic_ai_shields import BudgetExceededError

            if isinstance(e, BudgetExceededError):
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
                return
        except ImportError:  # pragma: no cover
            pass

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
        return

    # Extract usage from the completed run, if available.
    try:
        if agent_run.result is not None:
            u = agent_run.result.usage()
            usage_total["input"] = int(getattr(u, "input_tokens", 0) or 0)
            usage_total["output"] = int(getattr(u, "output_tokens", 0) or 0)
    except Exception:  # pragma: no cover
        pass

    # Compute dollar cost from model pricing catalog — persisted on the
    # assistant message and aggregated by /metrics/usage.
    cost_info = calc_cost_usd(
        resolved.model_name,
        resolved.provider_kind,
        usage_total.get("input"),
        usage_total.get("output"),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    usage_payload_out = {
        "tokens": usage_total,
        "cost": cost_info["cost"],
        "cost_currency": "USD",
        "cost_matched_model": cost_info["matched_model"],
        "latency_ms": latency_ms,
        "provider": resolved.provider_kind,
        "model": resolved.model_name,
    }
    yield RunEvent(RunEventKind.USAGE, usage_payload_out)
    yield RunEvent(
        RunEventKind.FINAL,
        {
            "message_id": final_message_id,
            "summary": None,
            "provider": resolved.provider_kind,
            "model": resolved.model_name,
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


# ─── Helpers ──────────────────────────────────────────────────
def _assemble_prompt(req: RunRequest, *, memory_fragment: str | None = None) -> str:
    from app.agents.prompts import assemble_system

    persona = (req.policy or {}).get("persona_md") if req.policy else None
    coding_fragment = build_coding_prompt_fragment(req.policy)
    # Fold coding-specific context (repo AGENTS.md / planning protocol /
    # verification hint) into the memory block so the assembler's existing
    # structure is reused verbatim.
    fragment = memory_fragment
    if coding_fragment:
        fragment = coding_fragment if not fragment else f"{coding_fragment}\n\n{fragment}"
    return assemble_system(persona, memory_fragment=fragment)


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
