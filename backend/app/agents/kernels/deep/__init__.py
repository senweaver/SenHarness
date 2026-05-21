"""Deep harness backend — **design stub, not implemented in this phase**.

Why this file exists
====================

The ``pydantic-deep`` aggregate package bundles every harness capability we
already use individually (filesystem, todo, subagents, skills, memory,
shields, sandbox, summarisation, cost-tracking, context-discovery) plus the
three add-ons we wired in P1 (plan / stuck-loop / checkpoint). Adopting it
"as-is" via :func:`pydantic_deep.create_deep_agent` would be tempting because
it ships a tuned ``BASE_PROMPT`` and a known-good capability composition.

We deliberately **do not** drop it in directly because every assumption it
bakes in is **single-tenant**:

- One backend instance (no per-workspace filesystem isolation).
- One ``MEMORY.md`` file (cross-tenant memory leak).
- ``interrupt_on={"tool":"msg"}`` is a constant — our ``ToolGuard`` decides
  per-workspace per-args.
- Subagents share the parent's backend handle directly, no wrapper.
- ``BASE_PROMPT`` replaces our SOUL composition; including both requires
  manual concatenation.

Plan (P3 candidate)
===================

When the cost/benefit tilts (e.g. ``pydantic-deep`` ships a feature we don't
have and don't want to clone), introduce ``DeepKernel`` here as an
``AgentBackend`` that:

1. Registers under ``backend_kind == "deep"`` via
   :mod:`app.agents.kernels.registry`. The DB column already accepts free
   strings (see :class:`app.db.models.agent.BackendKind`) so no migration
   is needed.
2. Internally calls ``pydantic_deep.create_deep_agent(...)`` but **never**
   forwards the user's raw policy. Instead it:

      - Wraps the supplied workspace backend so every filesystem op is
        rewritten through ``filesystem_sandbox`` rooted at
        ``STORAGE_LOCAL_PATH/scratch/<workspace_id>/<session_id>``.
      - Replaces ``interrupt_on`` with a thin shim that calls our
        ``ToolGuard`` (see :mod:`app.agents.harness.shields`) so the
        existing ``approval_request`` / ``approval_decision`` WS round trip
        still drives the gate.
      - Translates the deep ``MEMORY.md`` writes to our ``memory_profile``
        table keyed by ``(workspace_id, agent_id, identity_id)``.
      - Converts deep ``CostTracker`` events to ``RunEventKind.USAGE``
        frames so the existing observability / billing pipeline keeps
        working unchanged.

3. Exposes the same ``RunEvent`` stream so the ``/sessions/ws/{id}``
   handler doesn't care which kernel produced the events.

Until that lands, agents created with ``backend_kind="deep"`` will hit the
``kernel.backend_missing`` path because nothing is registered. That is
intentional — we'd rather refuse to run than silently route through an
unsafe single-tenant default.

If you find yourself reaching for this module
=============================================

Re-read [`docs/architecture.md`](../../../docs/architecture.md) on the
multi-tenant guarantees first. It is almost always cheaper to land the
missing capability in :mod:`app.agents.harness.*` than to wrap an entire
aggregate package and re-derive its safety properties.
"""

from __future__ import annotations

# This module intentionally registers nothing. Importing it is a no-op so
# operators who accidentally enable an agent with ``backend_kind="deep"``
# get the kernel.backend_missing error path with a clear log line, rather
# than a runtime that silently drops the workspace isolation.
__all__: list[str] = []
