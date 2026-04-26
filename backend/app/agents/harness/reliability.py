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
from typing import Any

log = logging.getLogger(__name__)


# ─── Defaults ────────────────────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "stuck_loop_detect": True,
    "stuck_loop_window": 6,          # consider last 6 tool calls
    "stuck_loop_threshold": 3,       # same signature appearing >=3 times → stuck

    "tool_error_recovery": True,
    "tool_error_max_retries": 2,     # per tool name, whole-run
    "tool_error_backoff_base_ms": 250,

    "orphan_repair": True,

    "adaptive_reasoning": False,     # opt-in: can affect cost/latency
    "adaptive_reasoning_low_threshold": 120,   # <120 chars user text → low
    "adaptive_reasoning_high_threshold": 800,  # >800 chars → high

    "limit_warnings": True,
    "limit_warning_ratio": 0.8,      # 80% of iteration budget

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
@dataclass(slots=True)
class ReliabilityState:
    """Mutable tracker for a single Agent run. Not thread-safe; one per run."""

    policy: dict[str, Any] = field(default_factory=dict)
    iteration_count: int = 0
    max_iterations: int = 12

    # Tool-call signature rolling window for stuck-loop detection.
    _recent_tools: deque[str] = field(default_factory=lambda: deque(maxlen=32))

    # Per-tool retry counters for error recovery.
    _retries_by_tool: dict[str, int] = field(default_factory=dict)

    # Emitted-once flags (avoid duplicate warnings).
    _limit_warning_emitted: bool = False
    _last_reminder_at: int = 0

    def tick_iteration(self) -> None:
        self.iteration_count += 1

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


# ─── Public entry points ─────────────────────────────────────────
def build_state(
    *,
    policy: dict[str, Any] | None,
    max_iterations: int,
) -> ReliabilityState:
    return ReliabilityState(policy=policy or {}, max_iterations=max_iterations)


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
    return new_value, True, {
        "shown": max_chars,
        "total": len(as_json),
        "full_payload": as_json,
    }


def repair_orphan_tool_calls(history: list[Any]) -> list[Any]:
    """Drop any ModelResponse ``ToolCallPart`` whose paired tool_return is
    missing from subsequent messages. pydantic-ai rejects such histories.

    This is a best-effort walk — we only touch pydantic-ai message objects.
    """
    if not history:
        return history
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            ToolCallPart,
            ToolReturnPart,
        )
    except ImportError:  # pragma: no cover
        return history

    # Pass 1 — collect all tool_call_ids that have a matching tool_return.
    returned_ids: set[str] = set()
    for msg in history:
        if isinstance(msg, ModelRequest):
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolReturnPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid:
                        returned_ids.add(str(tid))

    # Pass 2 — filter ToolCallParts whose id is missing a return.
    new_history: list[Any] = []
    dropped = 0
    for msg in history:
        if isinstance(msg, ModelResponse):
            kept_parts = []
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolCallPart):
                    tid = getattr(part, "tool_call_id", None)
                    if tid and str(tid) not in returned_ids:
                        dropped += 1
                        continue
                kept_parts.append(part)
            if kept_parts:
                new_history.append(ModelResponse(parts=kept_parts))
            else:
                # Response with only orphans → drop the whole message.
                dropped += 1
        else:
            new_history.append(msg)
    if dropped:
        log.info("orphan_repair: dropped %d orphan tool_call parts", dropped)
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
