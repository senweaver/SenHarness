"""Todo / task planning harness via ``pydantic-ai-todo``.

Opt-in per agent via ``metadata_json.todos``:

  - ``true`` / ``"enabled"``  → attach ``TodoCapability`` with an in-memory store
  - ``{"enable_subtasks": true}`` → also enable nested subtasks
  - omitted / ``false``       → no todo tools

Todos live **per run** (in-memory). Phase 3+ will migrate to PG-backed storage
scoped to session/agent/workspace when we want persistent plans.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def build_todo_capability(*, policy: dict[str, Any] | None) -> Any | None:
    spec = (policy or {}).get("todos")
    if not spec:
        return None

    try:
        from pydantic_ai_todo import AsyncMemoryStorage, TodoCapability
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-todo not installed; todos disabled")
        return None

    enable_subtasks = bool(
        spec.get("enable_subtasks")
        if isinstance(spec, dict)
        else False
    )

    try:
        storage = AsyncMemoryStorage()
        return TodoCapability(async_storage=storage, enable_subtasks=enable_subtasks)
    except Exception as e:  # pragma: no cover
        log.warning("TodoCapability init failed: %s", e)
        return None
