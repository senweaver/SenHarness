"""Ephemeral reflection injection + audit helpers.

The injection path (M0.4 / M0.5) inserts a transient ``SystemPromptPart`` into
the *next* model request without touching anything that survives the run:

* The ephemeral part is appended to ``ModelRequestNode.request.parts`` *before*
  the runner streams the node. Pydantic-ai's ``_prepare_request`` then folds
  the new part into the in-memory ``state.message_history`` for that single
  ``Agent.iter()`` call only.
* The runner never persists ``state.message_history`` to disk — only
  ``RunEvent`` deltas (text, tool calls, final) flow into the WebSocket
  pipeline and into the ``messages`` table. As long as we only mutate
  ``ModelRequestNode.request.parts`` (and never the ``messages`` row payloads
  that feed the next turn's ``_rehydrate_history``) the persisted prefix the
  next turn replays is byte-identical to the prefix this turn replayed —
  which is exactly the contract provider-side prompt caches need.

Audit rows describe *that* a reflection fired (kind, iteration, prompt char
length, whether it was truncated) but never carry the prompt body. That keeps
the audit log signal-to-noise high and avoids re-shipping a model-facing
template into a downstream pipeline that might persist it elsewhere.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.agents.harness.reliability import (
    ReflectionConfig,
    ReflectionKind,
    resolve_reflection_config,
)

log = logging.getLogger(__name__)


async def load_workspace_reflection_settings(
    workspace_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Read ``workspace.home_config_json`` for the reflection block.

    Returns an empty dict on any failure so the merger can fall back to
    agent-policy + built-in defaults. Opens its own short-lived session;
    the runner has no DB handle.
    """
    if workspace_id is None:
        return {}
    try:
        from app.db.session import get_session_factory
        from app.repositories.workspace import WorkspaceRepository
    except Exception:  # pragma: no cover
        return {}

    factory = get_session_factory()
    try:
        async with factory() as db:
            ws = await WorkspaceRepository(db).get(workspace_id)
            if ws is None:
                return {}
            home = ws.home_config_json or {}
            return home if isinstance(home, dict) else {}
    except Exception:  # pragma: no cover
        log.debug("workspace reflection settings load failed", exc_info=True)
        return {}


async def build_reflection_config(
    *,
    workspace_id: uuid.UUID | None,
    agent_policy: dict[str, Any] | None,
) -> ReflectionConfig:
    """Resolve the effective reflection config for one run.

    Convenience wrapper that fetches workspace settings and hands them, plus
    the agent policy, to :func:`resolve_reflection_config`. Lives here (not in
    ``reliability.py``) so the pure-policy path stays free of DB imports.
    """
    workspace_settings = await load_workspace_reflection_settings(workspace_id)
    return resolve_reflection_config(
        workspace_settings=workspace_settings,
        agent_policy=agent_policy,
    )


def inject_ephemeral_system_message(node: Any, prompt: str) -> bool:
    """Prepend ``prompt`` as a SystemPromptPart on the next model request.

    Returns ``True`` when the part landed; ``False`` for nodes we don't know
    how to mutate (placeholder runs, unsupported pydantic-ai versions). The
    caller decides whether to skip audit when injection fails.

    Important contract:

    * Mutates ``node.request.parts`` in place — this list is what
      ``_prepare_request`` appends to the per-iter ``state.message_history``,
      so the system message becomes visible to the model on this single call.
    * Does **not** touch ``message_history`` directly, ``capture_run_messages``,
      the ``messages`` DB rows, or anything used by ``_rehydrate_history``.
      The next user turn rebuilds prefix from DB; the reflection vanishes.
    """
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            RetryPromptPart,
            SystemPromptPart,
            ToolReturnPart,
        )
    except ImportError:  # pragma: no cover - prod always has pydantic-ai
        log.debug("pydantic-ai messages import failed; skipping reflection")
        return False

    request = getattr(node, "request", None)
    if not isinstance(request, ModelRequest):
        return False
    parts = getattr(request, "parts", None)
    if parts is None:
        return False

    # OpenAI-compatible providers (DeepSeek in particular) reject any
    # payload where an assistant ``tool_calls`` message isn't *immediately*
    # followed by tool messages for every ``tool_call_id``. If this
    # ``ModelRequest`` carries ``ToolReturnPart`` / tool-bound
    # ``RetryPromptPart``, prepending a ``SystemPromptPart`` would render
    # as ``[..., assistant(tool_calls), system, tool, tool, ...]`` and
    # trip ``insufficient tool messages following tool_calls`` HTTP 400.
    # Skip silently; the reflection trigger retries on the next request.
    for part in parts:
        if isinstance(part, ToolReturnPart):
            return False
        if isinstance(part, RetryPromptPart) and getattr(part, "tool_name", None):
            return False

    try:
        parts.insert(0, SystemPromptPart(content=prompt))
    except Exception:  # pragma: no cover - defensive
        log.debug("reflection part insert failed", exc_info=True)
        return False
    return True


async def audit_reflection(
    *,
    workspace_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    kind: ReflectionKind,
    iteration: int,
    tool_call_count: int,
    prompt_chars: int,
    truncated: bool,
) -> None:
    """Write one ``audit_events`` row per injection.

    Opens its own short-lived session — the runner has no DB handle and
    reflection is too rare to justify threading one in. Failures degrade
    silently because audit must never break the model loop.

    The metadata blob intentionally omits the rendered prompt: it would be
    high-volume noise (one big string per row) and creates a lateral leak
    risk if the audit log later fans out to less-trusted destinations.
    """
    if workspace_id is None:
        return
    try:
        from app.db.session import get_session_factory
        from app.services import audit as audit_svc
    except Exception:  # pragma: no cover
        return

    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="reflection.injected",
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="run",
                resource_id=run_id,
                summary=f"reflection {kind.value} fired at iter={iteration}",
                metadata={
                    "kind": kind.value,
                    "run_id": str(run_id) if run_id else None,
                    "session_id": str(session_id) if session_id else None,
                    "iteration": iteration,
                    "tool_call_count": tool_call_count,
                    "prompt_chars": prompt_chars,
                    "truncated": bool(truncated),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.debug("reflection audit failed", exc_info=True)
