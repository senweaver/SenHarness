"""Visual Flow execution engine (D14-P1).

The engine executes a DAG of typed nodes. Each node has:

* ``id``      — stable identifier referenced in templates + traces
* ``type``    — dispatches to a runner in ``app.services.flow_nodes``
* ``data``    — runner-specific configuration (prompt_template, url, etc.)

Contract:

1. :func:`topo_order` returns a valid topological order or raises ``CycleError``.
2. :func:`run_graph` executes nodes serially in that order, keeping a
   ``context`` dict ``{<node_id>: <output>}`` + ``start`` alias for the
   trigger payload.
3. After each node starts / ends we append an entry to
   ``FlowRun.node_events_json`` so the frontend can poll and animate.

Serial execution is a deliberate P1 limitation — even branches that could
run in parallel currently execute sequentially. P2 swaps the loop for
``asyncio.gather`` on ready nodes.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.core.security import utcnow_naive
from app.db.models.flow import FlowRun, FlowRunStatus
from app.db.session import get_session_factory
from app.repositories.flow import FlowRunRepository
from app.services.flow_nodes import NODE_RUNNERS, NodeContext

log = logging.getLogger(__name__)


# ─── Exceptions ──────────────────────────────────────────
class CycleError(RuntimeError):
    """Graph contains a cycle — rejected before execution."""


class UnknownNodeTypeError(RuntimeError):
    pass


# ─── Topological sort ────────────────────────────────────
def topo_order(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Kahn's algorithm. Returns node ids in dependency order.

    Raises :class:`CycleError` if a cycle is present; raises
    :class:`ValueError` if an edge references an unknown node.
    """
    node_ids = {n.get("id") for n in nodes if n.get("id")}
    indeg: dict[str, int] = dict.fromkeys(node_ids, 0)
    out_edges: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for e in edges:
        src, dst = e.get("source"), e.get("target")
        if src not in node_ids or dst not in node_ids:
            raise ValueError(f"edge references unknown node: {src} -> {dst}")
        out_edges[src].append(dst)
        indeg[dst] += 1

    ready = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for nxt in out_edges[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)

    if len(order) != len(node_ids):
        raise CycleError(f"cycle detected; {len(node_ids) - len(order)} nodes unresolved")
    return order


# ─── Template rendering ──────────────────────────────────
# Supports ``{{<node_id>.<field>[.<nested>]}}`` with best-effort dot paths.
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render a template against the node-output context.

    Missing paths render as empty string. Values that aren't strings are
    passed through ``str()``.
    """
    if not template:
        return ""

    def _sub(m: re.Match[str]) -> str:
        parts = m.group(1).split(".")
        cur: Any = context
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                cur = None
                break
            if cur is None:
                break
        return "" if cur is None else str(cur)

    return _TEMPLATE_RE.sub(_sub, template)


# ─── Run driver ──────────────────────────────────────────
async def run_graph(
    *,
    flow_run_id: uuid.UUID,
    graph: dict,
    trigger_payload: dict[str, Any],
    workspace_id: uuid.UUID,
    session_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
) -> None:
    """Execute ``graph`` and update the FlowRun row as it progresses.

    Opens its own DB session — designed to be called from an asyncio task.
    """
    nodes: list[dict] = list(graph.get("nodes") or [])
    edges: list[dict] = list(graph.get("edges") or [])

    factory = get_session_factory()

    # Phase 1: validate. Topo errors / unknown types fail the run immediately.
    try:
        order = topo_order(nodes, edges)
    except CycleError as e:
        await _finalize_failure(flow_run_id, error=f"cycle_detected: {e}")
        return
    except ValueError as e:
        await _finalize_failure(flow_run_id, error=f"graph_invalid: {e}")
        return

    nodes_by_id: dict[str, dict] = {n["id"]: n for n in nodes if n.get("id")}
    parents_of: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for e in edges:
        parents_of[e["target"]].append(e["source"])

    # Verify every node type is known before we start flipping statuses.
    for n in nodes:
        if n.get("type") not in NODE_RUNNERS:
            await _finalize_failure(
                flow_run_id,
                error=f"unknown_node_type: {n.get('type')!r}",
            )
            return

    context: dict[str, Any] = {"start": trigger_payload or {}}
    events: list[dict] = []

    async def _append_event(ev: dict) -> None:
        events.append(ev)
        async with factory() as db:
            repo = FlowRunRepository(db)
            row = await repo.get(flow_run_id)
            if row is not None:
                row.node_events_json = list(events)
                await db.flush([row])
                await db.commit()

    final_output: str | None = None

    for node_id in order:
        node = nodes_by_id[node_id]
        ntype = node["type"]
        node_data = node.get("data") or {}

        # ``start`` is a synthetic node that just exposes the trigger payload
        # under its own id. Let the runner return the payload so the context
        # is keyed symmetrically.
        node_input: dict[str, Any] = {
            "parents": {pid: context.get(pid, {}) for pid in parents_of.get(node_id, [])},
            "data": node_data,
        }

        event: dict[str, Any] = {
            "node_id": node_id,
            "type": ntype,
            "status": "running",
            "started_at": _now_iso(),
            "finished_at": None,
            "input": _truncate(node_input),
            "output": None,
            "error": None,
        }
        await _append_event(event)

        try:
            nctx = NodeContext(
                node_id=node_id,
                node_type=ntype,
                data=node_data,
                context=context,
                workspace_id=workspace_id,
                session_id=session_id,
                identity_id=identity_id,
                trigger_payload=trigger_payload,
                render=render_template,
            )
            runner = NODE_RUNNERS[ntype]
            output = await runner(nctx)
            context[node_id] = output if isinstance(output, dict) else {"value": output}

            # ``end`` nodes may return a text to surface on the FlowRun row.
            if ntype == "end":
                final_output = output.get("text") if isinstance(output, dict) else None

            event["status"] = "success"
            event["output"] = _truncate(output)
        except Exception as e:
            log.exception("flow node %s (%s) failed", node_id, ntype)
            event["status"] = "failed"
            event["error"] = str(e)[:2000]
            event["finished_at"] = _now_iso()
            # Replace the last event with the failed version.
            events[-1] = event
            await _finalize_failure(
                flow_run_id,
                error=f"{node_id} ({ntype}): {e}",
                events_override=events,
                output_summary=final_output,
            )
            return

        event["finished_at"] = _now_iso()
        events[-1] = event

    # All nodes succeeded.
    async with factory() as db:
        row = await FlowRunRepository(db).get(flow_run_id)
        if row is not None:
            row.status = FlowRunStatus.SUCCEEDED
            row.finished_at = utcnow_naive()
            row.output_summary = (final_output or "")[:1000] or None
            row.node_events_json = list(events)
            await db.flush([row])
            await db.commit()


# ─── Helpers ──────────────────────────────────────────────
async def _finalize_failure(
    flow_run_id: uuid.UUID,
    *,
    error: str,
    events_override: Iterable[dict] | None = None,
    output_summary: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        repo = FlowRunRepository(db)
        row: FlowRun | None = await repo.get(flow_run_id)
        if row is None:
            return
        row.status = FlowRunStatus.FAILED
        row.finished_at = utcnow_naive()
        row.error = error[:2000]
        if output_summary is not None:
            row.output_summary = output_summary[:1000]
        if events_override is not None:
            row.node_events_json = list(events_override)
        await db.flush([row])
        await db.commit()


def _now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _truncate(v: Any, max_chars: int = 2000) -> Any:
    """Clip oversized strings / dicts so the events table doesn't grow huge."""
    if isinstance(v, str):
        return v if len(v) <= max_chars else v[:max_chars] + "…"
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        size = 0
        for k, val in v.items():
            sv = (
                val
                if not isinstance(val, str)
                else (val if len(val) <= max_chars else val[:max_chars] + "…")
            )
            out[k] = sv
            size += 1
            if size >= 32:
                out["_truncated"] = True
                break
        return out
    return v
