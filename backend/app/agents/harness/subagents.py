"""Sub-agent delegation harness.

Wraps ``subagents-pydantic-ai`` as a pydantic-ai ``Capability`` and lets the
main agent spawn focused workers for multi-step or parallelizable tasks.

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
"""

from __future__ import annotations

import logging
from typing import Any

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

    ``SubAgentConfig`` (a TypedDict in subagents-pydantic-ai) requires both
    ``description`` and ``instructions``. We fill ``instructions`` from the
    description when callers don't supply one explicitly.
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
