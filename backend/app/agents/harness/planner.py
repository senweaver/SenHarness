"""Plan-mode capability — injects a ``planner`` sub-agent.

Activated by ``policy.plan = true`` (set on the runtime side when the user
selects the Plan mode in the chat composer). Re-uses the existing
``subagents-pydantic-ai`` capability so the planner shows up as an extra
``task`` target the main agent can delegate to.

The planner's instructions live in
[`backend/app/agents/templates/planner.md`](../templates/planner.md) — pure
Markdown so product owners can iterate without touching Python.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


PLANNER_NAME = "planner"
PLANNER_DESCRIPTION = (
    "Plan-only specialist. Turns vague requests into concrete numbered steps, "
    "calls out risks and assumptions, and never executes tools itself. "
    "Delegate to it before kicking off multi-step work."
)


def _read_template() -> str:
    """Load planner instructions from the template file.

    Falls back to a short inline default so a missing template file doesn't
    silently disable the capability — operators can override either way.
    """
    here = Path(__file__).resolve().parent.parent / "templates" / "planner.md"
    try:
        return here.read_text(encoding="utf-8")
    except OSError:
        log.info("planner template not found at %s; using inline fallback", here)
        return (
            "You are a planner. Produce a numbered Markdown plan with Goal, "
            "Assumptions, Steps, Risks, and Success criteria. Do not execute "
            "tools. Stay terse (≤ 12 steps)."
        )


def planner_subagent_spec() -> dict[str, Any]:
    """Return a ``SubAgentConfig``-shaped dict describing the planner."""
    return {
        "name": PLANNER_NAME,
        "description": PLANNER_DESCRIPTION,
        "instructions": _read_template(),
        "preferred_mode": "plan",
        "typical_complexity": "medium",
    }


def merge_planner_into_subagents(policy: dict[str, Any]) -> dict[str, Any]:
    """Patch a runner ``policy`` so the planner subagent is registered.

    Idempotent: if a planner spec is already present we leave it alone (lets
    operators override description/instructions per agent). Returns a *new*
    dict so the caller can safely use it without mutating the original.
    """
    new_policy = dict(policy or {})
    if not bool(new_policy.get("plan")):
        return new_policy

    raw_sub = new_policy.get("subagents")
    if raw_sub is False:
        # Operator explicitly disabled subagents — don't surreptitiously
        # re-enable them just because plan=true.
        return new_policy

    if raw_sub is None or raw_sub is True:
        sub: dict[str, Any] = {
            "enabled": True,
            "include_general_purpose": True,
            "specs": [],
        }
    elif isinstance(raw_sub, dict):
        sub = dict(raw_sub)
        sub.setdefault("enabled", True)
        sub.setdefault("specs", [])
        if not isinstance(sub["specs"], list):
            sub["specs"] = []
    elif isinstance(raw_sub, list):
        sub = {"enabled": True, "include_general_purpose": True, "specs": list(raw_sub)}
    else:
        return new_policy

    specs = list(sub.get("specs") or [])
    if any(
        isinstance(s, dict) and str(s.get("name", "")).lower() == PLANNER_NAME
        for s in specs
    ):
        # Already configured — caller wins.
        return new_policy

    specs.append(planner_subagent_spec())
    sub["specs"] = specs
    new_policy["subagents"] = sub
    return new_policy
