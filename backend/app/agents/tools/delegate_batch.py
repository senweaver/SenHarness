"""``delegate_batch`` builtin — fan-out N parallel sub-agents (M2.5.6).

Lets a parent agent spawn many focused worker children in one tool call
and aggregate their final outputs. Internally calls the harness layer's
:func:`app.agents.harness.subagents.delegate_batch` so all reliability
invariants (M2.5.1 spine row + heartbeat + retry budget + hallucination
gate; this milestone's nesting depth + concurrency cap + serial
fallback) ride along automatically.

Wire shape:

* Caller passes ``tasks: [{task_id, prompt, target_agent_id, ...}]`` —
  ``target_agent_id`` must be an Agent in the same workspace.
* Returns the :class:`BatchSpawnResult` envelope as a JSON-safe dict.
  The ``results`` map is keyed on the caller-supplied ``task_id`` so
  the parent reasons in its own naming scheme.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.harness import subagents as subagents_svc
from app.agents.tools._context import get_context

log = logging.getLogger(__name__)


__all__ = [
    "DelegateBatchArgs",
    "DelegateBatchTaskIn",
    "run_delegate_batch",
]


class DelegateBatchTaskIn(BaseModel):
    """One task in the batch payload.

    Field bounds match :class:`SubAgentTask` — ``task_id`` 1–80 chars,
    ``prompt`` 1–4000 chars, ``timeout_seconds`` 1–600. ``inherit_skills``
    defaults to ``True`` because workspace skills are scoped configuration;
    ``inherit_memory`` defaults to ``False`` so personal long-term notes
    don't leak into spawned helpers (privacy default — see roadmap
    design principle 6).
    """

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=4000)
    target_agent_id: uuid.UUID
    timeout_seconds: int = Field(ge=1, le=600, default=300)
    inherit_skills: bool = True
    inherit_memory: bool = False


class DelegateBatchArgs(BaseModel):
    """Tool args for ``delegate_batch``.

    ``max_concurrent`` is bounded above by the workspace's resolved
    ``max_concurrent`` (default 5/parent) — a parent that asks for
    higher concurrency than the workspace permits is silently clamped
    by the service layer and the audit row carries the effective
    value.
    """

    model_config = ConfigDict(populate_by_name=True)

    tasks: list[DelegateBatchTaskIn] = Field(min_length=1, max_length=20)
    max_concurrent: int | None = Field(default=None, ge=1, le=10)


async def run_delegate_batch(args: DelegateBatchArgs) -> dict[str, Any]:
    """Workspace-scoped: dispatch the batch + return the aggregated dict.

    Errors:

    * Cross-workspace ``target_agent_id`` is rejected at the per-child
      resolver layer (the child returns ``rejected/agent_not_found``).
      We do not pre-validate here because the pre-flight DB roundtrip
      would double the cost of common N-task batches; the per-child
      ``rejected`` slot in the result map is the right surface anyway.
    * ``ValueError`` from duplicate ``task_id`` is surfaced as a
      structured tool result the agent can read + retry on; everything
      else above the gather() boundary is captured into a
      ``status='failed'`` per-child slot.
    """
    ctx = get_context()

    tasks = [
        subagents_svc.SubAgentTask(
            task_id=item.task_id,
            prompt=item.prompt,
            target_agent_id=item.target_agent_id,
            timeout_seconds=int(item.timeout_seconds),
            inherit_skills=bool(item.inherit_skills),
            inherit_memory=bool(item.inherit_memory),
        )
        for item in args.tasks
    ]

    spawn_depth = 1
    if isinstance(ctx.policy, dict):
        raw_depth = ctx.policy.get("subagent_spawn_depth")
        if isinstance(raw_depth, int) and raw_depth >= 0:
            spawn_depth = raw_depth + 1

    try:
        summary = await subagents_svc.delegate_batch(
            parent_run_id=ctx.run_id,
            workspace_id=ctx.workspace_id,
            parent_session_id=ctx.session_id,
            parent_identity_id=ctx.identity_id,
            tasks=tasks,
            max_concurrent=args.max_concurrent,
            spawn_depth=spawn_depth,
        )
    except ValueError as exc:
        return {
            "status": "rejected",
            "code": "subagent.batch_invalid_args",
            "message": str(exc),
        }

    return _envelope_to_dict(summary)


def _envelope_to_dict(summary: subagents_svc.BatchSpawnResult) -> dict[str, Any]:
    """Render the BatchSpawnResult into a JSON-safe nested dict.

    The pydantic ``model_dump(mode='json')`` path is the single source
    of truth for shape + type coercion. Per-child ``SubAgentResult``
    instances are dataclasses so we explicitly call ``to_dict`` on each
    to get UUID strings without bypassing model validation.
    """
    payload = summary.model_dump(mode="json")
    payload["results"] = {task_id: result.to_dict() for task_id, result in summary.results.items()}
    return payload
