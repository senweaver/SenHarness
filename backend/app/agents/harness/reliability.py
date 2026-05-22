"""Agent runtime reliability layer — seven hooks that prevent an Agent run
from silently looping, exploding, or stalling.

Each hook is gated by a policy flag so operators can opt in/out per agent:

    reliability:
      stuck_loop_detect: true           # repeated-tool-call breaker
      tool_error_recovery: true         # retry + backoff on transient tool errors
      orphan_repair: true               # strip tool_call_id w/o matching result
      adaptive_reasoning: false         # auto-pick reasoning_effort from prompt
      limit_warnings: true              # inject warning at 80% of iteration budget
      tool_output_overflow: true        # truncate huge tool outputs
      system_reminders: false           # re-inject short reminder every N iters

The `ReliabilityState` is owned by one run. Runner consults it between
iterations/tool calls and optionally emits extra `RunEvent`s (warnings /
reminders) into the stream.

All features are *additive*: if `reliability` block is missing from policy,
every feature falls back to a sensible default (mostly on for safety).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)


class ReflectionKind(StrEnum):
    """Why a reflection prompt was injected.

    PERIODIC fires every ``interval_iterations`` graph nodes (M0.4); TOOL_CALL
    fires every ``interval_tool_calls`` accumulated tool calls (M0.5). The two
    paths are mutually exclusive within a single iteration — whichever
    threshold trips first wins, the loser is suppressed until the next iter.
    """

    PERIODIC = "periodic"
    TOOL_CALL = "tool_call"


@dataclass(slots=True)
class ReflectionConfig:
    """Per-run reflection knobs. Default values are used when neither agent
    policy nor workspace settings supply an override."""

    enabled: bool = True
    interval_iterations: int = 8
    interval_tool_calls: int = 15
    max_prompt_chars: int = 800
    periodic_template: str = "periodic"
    tool_call_template: str = "tool_call"


@dataclass(slots=True)
class ReflectionDecision:
    """Outcome of a single ``should_reflect()`` consultation.

    ``rendered_prompt`` is already truncated to ``max_prompt_chars`` and ready
    to drop into a SystemPromptPart. ``reason`` is a short debug tag so audit
    rows / log lines can explain why injection did or did not happen without
    leaking the prompt body.
    """

    should_inject: bool
    kind: ReflectionKind | None = None
    rendered_prompt: str | None = None
    reason: str | None = None
    truncated: bool = False


# ─── Defaults ────────────────────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "stuck_loop_detect": True,
    "stuck_loop_window": 6,  # consider last 6 tool calls
    "stuck_loop_threshold": 3,  # same signature appearing >=3 times → stuck
    "stuck_loop_abort": True,  # hard-abort the run (vs only emit warning)
    "tool_error_recovery": True,
    "tool_error_max_retries": 2,  # per tool name, whole-run
    "tool_error_backoff_base_ms": 250,
    "orphan_repair": True,
    "adaptive_reasoning": False,  # opt-in: can affect cost/latency
    "adaptive_reasoning_low_threshold": 120,  # <120 chars user text → low
    "adaptive_reasoning_high_threshold": 800,  # >800 chars → high
    "limit_warnings": True,
    "limit_warning_ratio": 0.8,  # 80% of iteration budget
    "tool_output_overflow": True,
    "tool_output_max_chars": 4000,
    "tool_output_overflow_hint": (
        "⚠ tool output truncated to {shown}/{total} chars; "
        "the full payload was saved to scratch/tool_output_{call_id}.json"
    ),
    "system_reminders": False,
    "system_reminder_every_iters": 5,
}


def _cfg(policy: dict[str, Any] | None, key: str) -> Any:
    """Read a reliability config key with fallback to _DEFAULTS."""
    if policy:
        block = policy.get("reliability") or {}
        if key in block:
            return block[key]
    return _DEFAULTS[key]


# ─── Per-run state ───────────────────────────────────────────────
class StuckLoopAbort(RuntimeError):
    """Raised by the runner when the same tool call is fired ``stuck_loop_threshold``
    times within ``stuck_loop_window`` and the policy asks us to hard-abort the
    run instead of just nudging the model.

    The runner catches this and emits ``error { code='stuck_loop' }`` + ``final``
    so the WS UI can render a graceful message and unlock the input.
    """

    def __init__(self, tool_name: str, count: int, threshold: int) -> None:
        super().__init__(
            f"stuck_loop: tool {tool_name!r} called {count} times (threshold {threshold})"
        )
        self.tool_name = tool_name
        self.count = count
        self.threshold = threshold


@dataclass(slots=True)
class ReliabilityState:
    """Mutable tracker for a single Agent run. Not thread-safe; one per run."""

    policy: dict[str, Any] = field(default_factory=dict)
    iteration_count: int = 0
    max_iterations: int = 12

    # Tool-call signature rolling window for stuck-loop detection.
    _recent_tools: deque[str] = field(default_factory=lambda: deque(maxlen=32))

    # Last-N tool-call summary for the M0.5 reflection prompt template; richer
    # than ``_recent_tools`` (carries name + arg digest + ok flag) so the
    # rendered prompt can show concrete evidence to the model.
    _recent_tool_records: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=10))

    # Per-tool retry counters for error recovery.
    _retries_by_tool: dict[str, int] = field(default_factory=dict)

    # Emitted-once flags (avoid duplicate warnings).
    _limit_warning_emitted: bool = False
    _stuck_emitted: bool = False
    _last_reminder_at: int = 0

    # ─── M0.4 / M0.5 reflection ───────────────────────────
    # Counters and last-fired markers live in memory only — they reset every
    # run, so a 9-iteration first turn followed by a 1-iteration second turn
    # never carries reflection debt across turns. The "last fired" markers
    # default to 0 so the first PERIODIC injection happens at iter ==
    # ``interval_iterations`` (not at iter == interval - 1, which off-by-one
    # would happen with a -1 sentinel).
    tool_call_count: int = 0
    last_reflection_iteration: int = 0
    last_reflection_tool_count: int = 0
    reflection_config: ReflectionConfig | None = None

    def tick_iteration(self) -> None:
        self.iteration_count += 1

    def tick_tool_call(self) -> None:
        """Bump the tool-call counter used by the M0.5 reflection trigger."""
        self.tool_call_count += 1

    def record_tool_outcome(self, tool_name: str, args: dict | None, *, ok: bool) -> None:
        """Append a (name, args, ok) tuple for the tool_call reflection
        template. Cheap; runs every tool result in the runner."""
        try:
            args_preview = _short_args(args)
        except Exception:  # pragma: no cover - defensive
            args_preview = ""
        self._recent_tool_records.append({"name": tool_name, "args": args_preview, "ok": ok})

    def recent_tool_summary(self, max_count: int = 5) -> list[dict[str, Any]]:
        """Return the most recent ``max_count`` tool calls (name, args, ok).

        Newest last, so a template can render them as a chronological list.
        """
        if max_count <= 0:
            return []
        items = list(self._recent_tool_records)
        return items[-max_count:]

    def should_reflect(self) -> ReflectionDecision:
        """Decide whether the runner should inject a reflection on this iter.

        Honors:
            * ``reflection_config.enabled`` — disabled → silent no-op.
            * Periodic interval vs ``last_reflection_iteration``.
            * Tool-call interval vs ``last_reflection_tool_count``.
            * At most one injection per iteration; periodic wins ties.
            * ``max_prompt_chars`` truncates the rendered template.

        Sets the matching ``last_reflection_*`` marker before returning a
        positive decision so the same iteration cannot fire twice.
        Templates are loaded lazily inside the function to keep the disabled
        path a true zero-IO short-circuit.
        """
        cfg = self.reflection_config
        if cfg is None or not cfg.enabled:
            return ReflectionDecision(False, reason="skip:disabled")

        # Same-iteration de-dupe: never fire twice in one graph step.
        if self.last_reflection_iteration == self.iteration_count:
            return ReflectionDecision(False, reason="skip:already_this_iteration")

        kind: ReflectionKind | None = None
        if cfg.interval_iterations > 0 and (
            self.iteration_count - self.last_reflection_iteration >= cfg.interval_iterations
        ):
            kind = ReflectionKind.PERIODIC
        elif cfg.interval_tool_calls > 0 and (
            self.tool_call_count - self.last_reflection_tool_count >= cfg.interval_tool_calls
        ):
            kind = ReflectionKind.TOOL_CALL

        if kind is None:
            return ReflectionDecision(False, reason="skip:no_threshold_hit")

        # Lazy import keeps the module graph free of a hard template
        # dependency for callers that don't reflect at all (CLI, batch jobs).
        from app.agents.templates.reflection import (
            TemplateNotFoundError,
            load_reflection_template,
        )

        template_name = (
            cfg.periodic_template if kind == ReflectionKind.PERIODIC else cfg.tool_call_template
        )
        try:
            template_body = load_reflection_template(template_name)
        except TemplateNotFoundError:
            log.warning("reflection template missing: %s", template_name)
            return ReflectionDecision(False, reason="skip:template_missing")

        rendered = _render_template(
            template_body,
            iteration_count=self.iteration_count,
            tool_call_count=self.tool_call_count,
            recent_tools=self.recent_tool_summary(),
        )
        truncated = False
        if cfg.max_prompt_chars > 0 and len(rendered) > cfg.max_prompt_chars:
            rendered = rendered[: cfg.max_prompt_chars].rstrip()
            truncated = True

        if kind == ReflectionKind.PERIODIC:
            self.last_reflection_iteration = self.iteration_count
        else:
            self.last_reflection_tool_count = self.tool_call_count
            # Periodic is also blocked this iteration to keep the contract.
            self.last_reflection_iteration = self.iteration_count

        reason = (
            f"iter={self.iteration_count}"
            if kind == ReflectionKind.PERIODIC
            else f"tools={self.tool_call_count}"
        )
        if truncated:
            reason = f"{reason}|truncated"

        return ReflectionDecision(
            should_inject=True,
            kind=kind,
            rendered_prompt=rendered,
            reason=reason,
            truncated=truncated,
        )

    # ─── Stuck-loop detection ─────────────────────────────
    def record_tool_call(self, tool_name: str, args: dict | None) -> None:
        if not _cfg(self.policy, "stuck_loop_detect"):
            return
        self._recent_tools.append(_signature(tool_name, args))

    def is_stuck(self) -> tuple[bool, str]:
        """Return (True, repeated_tool_name) if the last N calls are mostly
        the same signature; otherwise (False, '').
        """
        if not _cfg(self.policy, "stuck_loop_detect"):
            return False, ""
        window = int(_cfg(self.policy, "stuck_loop_window"))
        threshold = int(_cfg(self.policy, "stuck_loop_threshold"))
        if len(self._recent_tools) < threshold:
            return False, ""
        tail = list(self._recent_tools)[-window:]
        counts: dict[str, int] = {}
        for sig in tail:
            counts[sig] = counts.get(sig, 0) + 1
        worst = max(counts, key=counts.get)  # type: ignore[arg-type]
        if counts[worst] >= threshold:
            return True, worst.split("|", 1)[0]
        return False, ""

    def stuck_count(self, tool_name: str) -> int:
        """Number of times ``tool_name`` (any arg-hash) appears in the window."""
        prefix = f"{tool_name}|"
        return sum(1 for s in self._recent_tools if s.startswith(prefix))

    def maybe_raise_stuck_loop(self) -> None:
        """If we're stuck and the policy asks for hard abort, raise StuckLoopAbort.

        Emits exactly once per run (further calls become no-ops). When abort
        is disabled the function is silent — the runner falls back to a
        thinking warning.
        """
        if self._stuck_emitted:
            return
        if not _cfg(self.policy, "stuck_loop_detect"):
            return
        if not bool(_cfg(self.policy, "stuck_loop_abort")):
            return
        stuck, repeated = self.is_stuck()
        if not stuck:
            return
        threshold = int(_cfg(self.policy, "stuck_loop_threshold"))
        count = self.stuck_count(repeated)
        self._stuck_emitted = True
        raise StuckLoopAbort(repeated, count, threshold)

    # ─── Tool error recovery ──────────────────────────────
    def should_retry_tool(self, tool_name: str) -> bool:
        """Bookkeeping for the runner: call after a tool produced an error.

        Returns True if the tool may be retried given the per-tool budget;
        the runner is responsible for actually sleeping + re-invoking.
        """
        if not _cfg(self.policy, "tool_error_recovery"):
            return False
        max_retries = int(_cfg(self.policy, "tool_error_max_retries"))
        used = self._retries_by_tool.get(tool_name, 0)
        if used >= max_retries:
            return False
        self._retries_by_tool[tool_name] = used + 1
        return True

    def backoff_ms(self, tool_name: str) -> int:
        used = self._retries_by_tool.get(tool_name, 1)
        base = int(_cfg(self.policy, "tool_error_backoff_base_ms"))
        return base * (2 ** max(0, used - 1))

    # ─── Iteration-budget warning ─────────────────────────
    def limit_warning(self) -> str | None:
        if not _cfg(self.policy, "limit_warnings"):
            return None
        if self._limit_warning_emitted:
            return None
        if self.max_iterations <= 0:
            return None
        ratio = self.iteration_count / self.max_iterations
        threshold = float(_cfg(self.policy, "limit_warning_ratio"))
        if ratio >= threshold:
            self._limit_warning_emitted = True
            return (
                f"⚠ 预算即将耗尽：已用 {self.iteration_count}/{self.max_iterations} 轮。"
                f"请尽快给出最终答复或缩小任务范围。"
            )
        return None

    # ─── System reminders ─────────────────────────────────
    def maybe_reminder(self) -> str | None:
        if not _cfg(self.policy, "system_reminders"):
            return None
        every = int(_cfg(self.policy, "system_reminder_every_iters"))
        if self.iteration_count - self._last_reminder_at < every:
            return None
        self._last_reminder_at = self.iteration_count
        return (
            "[reminder] Stay focused on the user's original goal. Use tools "
            "for facts; avoid fabrication; if stuck, ask a clarifying question."
        )


# ─── Helpers ─────────────────────────────────────────────────────
def _signature(tool_name: str, args: dict | None) -> str:
    """Canonical signature for stuck-loop detection. Keeps tool_name readable
    in front of a short args hash so logs are debuggable."""
    try:
        import json as _json

        raw = _json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        raw = str(args)
    h = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{tool_name}|{h}"


def _short_args(args: dict | None) -> str:
    """One-line preview of tool args for the reflection template.

    Caps at 80 chars so a single noisy arg can't blow out the prompt.
    Strings only — no PII rules; the template never escapes the agent loop.
    """
    if not args:
        return ""
    try:
        import json as _json

        raw = _json.dumps(args, default=str, ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = str(args)
    raw = raw.replace("\n", " ").strip()
    return raw[:80] + "…" if len(raw) > 80 else raw


def _render_template(
    body: str,
    *,
    iteration_count: int,
    tool_call_count: int,
    recent_tools: list[dict[str, Any]],
) -> str:
    """Render a reflection template by replacing the well-known placeholders.

    We use ``str.format_map`` with a defaultdict so an unknown placeholder
    becomes an empty string instead of raising — templates can evolve without
    coupling to runner internals.
    """
    summary_lines = [
        f"  - {item.get('name', '?')}({item.get('args', '')})"
        f" → {'ok' if item.get('ok') else 'error'}"
        for item in recent_tools
    ]
    summary = "\n".join(summary_lines) if summary_lines else "(none recorded)"
    mapping = _DefaultMapping(
        iteration_count=iteration_count,
        tool_call_count=tool_call_count,
        recent_tools_summary=summary,
    )
    try:
        return body.format_map(mapping)
    except Exception:
        # Bad template authors shouldn't crash the run; fall back to raw body.
        log.warning("reflection template render failed; using raw body")
        return body


class _DefaultMapping(dict):
    """``str.format_map`` mapping that returns ``""`` for missing keys."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def __missing__(self, key: str) -> str:
        return ""


# ─── Public entry points ─────────────────────────────────────────
def build_state(
    *,
    policy: dict[str, Any] | None,
    max_iterations: int,
    reflection_config: ReflectionConfig | None = None,
) -> ReliabilityState:
    state = ReliabilityState(policy=policy or {}, max_iterations=max_iterations)
    state.reflection_config = reflection_config
    return state


def resolve_reflection_config(
    *,
    workspace_settings: dict[str, Any] | None,
    agent_policy: dict[str, Any] | None,
) -> ReflectionConfig:
    """Merge workspace + agent overrides into a single :class:`ReflectionConfig`.

    Field precedence:
        * Tunable fields (``interval_iterations``, ``interval_tool_calls``,
          ``max_prompt_chars``, ``periodic_template``, ``tool_call_template``)
          follow ``agent > workspace > defaults``.
        * ``enabled`` is the **AND** of the workspace and agent flags.
          Either side setting ``enabled=False`` disables reflection for that
          agent. The workspace flag is therefore a true kill switch — agent
          authors cannot opt back in once the workspace admin has opted out.
          The workspace owns the cost / privacy budget for everything that
          runs under it; a kill switch must be authoritative.

    Unknown keys are ignored so future fields don't crash old runtimes.
    """
    base = ReflectionConfig()
    ws = _reflection_block(workspace_settings)
    ag = _reflection_block(agent_policy)

    workspace_enabled = bool(ws.get("enabled", base.enabled))
    agent_enabled = bool(ag.get("enabled", workspace_enabled))
    final_enabled = workspace_enabled and agent_enabled

    return ReflectionConfig(
        enabled=final_enabled,
        interval_iterations=_pick_int(ag, ws, "interval_iterations", base.interval_iterations),
        interval_tool_calls=_pick_int(ag, ws, "interval_tool_calls", base.interval_tool_calls),
        max_prompt_chars=_pick_int(ag, ws, "max_prompt_chars", base.max_prompt_chars),
        periodic_template=_pick_str(ag, ws, "periodic_template", base.periodic_template),
        tool_call_template=_pick_str(ag, ws, "tool_call_template", base.tool_call_template),
    )


def _reflection_block(source: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    block = source.get("reflection")
    return block if isinstance(block, dict) else {}


def _pick_int(high: dict[str, Any], low: dict[str, Any], key: str, default: int) -> int:
    """``high`` (agent) > ``low`` (workspace) > ``default``; non-int values
    fall through silently rather than crashing the run."""
    for source in (high, low):
        if key in source:
            try:
                return int(source[key])
            except (TypeError, ValueError):
                continue
    return default


def _pick_str(high: dict[str, Any], low: dict[str, Any], key: str, default: str) -> str:
    for source in (high, low):
        if key in source:
            return str(source[key])
    return default


def truncate_tool_output(
    value: Any,
    *,
    policy: dict[str, Any] | None,
    call_id: str,
) -> tuple[Any, bool, dict[str, Any]]:
    """Truncate a tool result payload if it exceeds the overflow cap.

    Returns (value, truncated, meta). ``meta`` carries ``{shown, total,
    overflow_file}`` when truncation happens so the runner can persist the
    full blob and emit a hint. Non-string / non-list / non-dict values pass
    through unchanged.
    """
    if not _cfg(policy, "tool_output_overflow"):
        return value, False, {}

    max_chars = int(_cfg(policy, "tool_output_max_chars"))
    try:
        import json as _json

        as_json = _json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        as_json = str(value)

    if len(as_json) <= max_chars:
        return value, False, {}

    truncated_json = as_json[:max_chars]
    hint = str(_cfg(policy, "tool_output_overflow_hint")).format(
        shown=max_chars, total=len(as_json), call_id=call_id
    )
    new_value: Any
    if isinstance(value, str):
        new_value = value[:max_chars] + "\n" + hint
    else:
        new_value = {"__truncated__": True, "preview": truncated_json, "hint": hint}
    return (
        new_value,
        True,
        {
            "shown": max_chars,
            "total": len(as_json),
            "full_payload": as_json,
        },
    )


def repair_orphan_tool_calls(history: list[Any]) -> list[Any]:
    """Strip unpaired tool_call/tool_return parts and thinking-only responses.

    OpenAI-compatible providers (DeepSeek in particular) reject any payload
    where an assistant ``tool_calls`` entry is missing a matching ``tool``
    message, or vice versa, with HTTP 400 ``insufficient tool messages``.
    The pairing can break for several reasons inside one run:

    * ``SlidingWindowProcessor`` trimming a long live history by message
      count, slicing through a ToolCall/ToolReturn pair.
    * A tool runner raising an exception that the kernel swallowed without
      synthesising a ToolReturnPart.
    * The model emitting parallel tool_calls where one never produces a
      result (timeout / cancel).

    Additionally, thinking-capable models (Qwen3, DeepSeek-R1, etc.) can
    produce ``ModelResponse`` entries that contain only ``ThinkingPart``s
    and no ``TextPart`` or ``ToolCallPart``. Empty / whitespace-only
    ``TextPart``s show up in the same scenario — the model opened a text
    part but never streamed real tokens before switching to a tool call
    or finalising. Either shape serialises to ``{"role": "assistant",
    "content": ""}`` (or null) on the OpenAI-compatible wire, which
    DeepSeek rejects with ``Invalid assistant message: content or
    tool_calls must be set``. We strip both here: ThinkingParts are
    ephemeral reasoning artefacts already encoded downstream, and an
    empty TextPart carries no information the upstream model can use.

    This walker is best-effort and idempotent: it only touches pydantic-ai
    message objects, and a clean history passes through untouched.
    """
    if not history:
        return history
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            ToolCallPart,
            ToolReturnPart,
        )
    except ImportError:  # pragma: no cover
        return history

    try:
        from pydantic_ai.messages import ThinkingPart as _ThinkingPart
    except ImportError:  # pragma: no cover — older pydantic-ai without thinking support
        _ThinkingPart = None

    called_ids: set[str] = set()
    returned_ids: set[str] = set()
    for msg in history:
        if isinstance(msg, ModelResponse):
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolCallPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid:
                        called_ids.add(str(tid))
        elif isinstance(msg, ModelRequest):
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolReturnPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid:
                        returned_ids.add(str(tid))

    new_history: list[Any] = []
    dropped_calls = 0
    dropped_returns = 0
    dropped_empty_responses = 0
    for msg in history:
        if isinstance(msg, ModelResponse):
            kept_parts = []
            for part in getattr(msg, "parts", []) or []:
                if _ThinkingPart is not None and isinstance(part, _ThinkingPart):
                    continue
                if isinstance(part, TextPart):
                    content = getattr(part, "content", "") or ""
                    if not str(content).strip():
                        continue
                if isinstance(part, ToolCallPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid and str(tid) not in returned_ids:
                        dropped_calls += 1
                        continue
                kept_parts.append(part)
            if kept_parts:
                new_history.append(ModelResponse(parts=kept_parts))
            else:
                dropped_empty_responses += 1
        elif isinstance(msg, ModelRequest):
            kept_parts = []
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolReturnPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid and str(tid) not in called_ids:
                        dropped_returns += 1
                        continue
                kept_parts.append(part)
            if kept_parts:
                new_history.append(ModelRequest(parts=kept_parts))
            else:
                dropped_returns += 1
        else:
            new_history.append(msg)
    if dropped_calls or dropped_returns or dropped_empty_responses:
        log.info(
            "orphan_repair: dropped %d orphan tool_call(s), %d orphan tool_return(s), "
            "%d empty assistant response(s)",
            dropped_calls,
            dropped_returns,
            dropped_empty_responses,
        )
    return new_history


# ─── Adaptive reasoning effort ───────────────────────────────────
_HIGH_KEYWORDS = re.compile(
    r"\b(plan|think|analy[sz]e|reason|design|architect|prove|derive|compare|optimi[sz]e)\b",
    re.IGNORECASE,
)


def pick_reasoning_effort(
    user_text: str,
    *,
    policy: dict[str, Any] | None,
) -> str | None:
    """Return ``"low"|"medium"|"high"`` or ``None`` (leave unset).

    Heuristic: prompt length + presence of analytical keywords. Keeps the
    decision transparent — operators can turn the feature off entirely.
    """
    if not _cfg(policy, "adaptive_reasoning"):
        return None
    text = (user_text or "").strip()
    low = int(_cfg(policy, "adaptive_reasoning_low_threshold"))
    high = int(_cfg(policy, "adaptive_reasoning_high_threshold"))
    if not text:
        return "low"
    if _HIGH_KEYWORDS.search(text):
        return "high"
    if len(text) >= high:
        return "high"
    if len(text) <= low:
        return "low"
    return "medium"


# ─── Bootstrap: register every feature with plugin_host on module import ───
# We keep `plugin_host` as the fan-out so third-party plugins can observe the
# same lifecycle events. Each hook is a light async no-op here; the heavy
# lifting stays in `ReliabilityState` so the runner owns the control flow.
def _register_default_hooks() -> None:
    from app.agents.harness import plugin_host

    async def _noop_pre_tool(**_: Any) -> None:
        return None

    async def _noop_post_tool(**_: Any) -> None:
        return None

    plugin_host.register("pre_tool_call", _noop_pre_tool)
    plugin_host.register("post_tool_call", _noop_post_tool)


_register_default_hooks()
