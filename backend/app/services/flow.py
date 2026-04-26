"""Flow execution service — manual, webhook, cron triggers."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.security import utcnow_naive
from app.db.models.flow import Flow, FlowRun, FlowRunStatus, FlowTriggerKind
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.repositories.session import SessionRepository
from app.services import agent_runner as runner

log = logging.getLogger(__name__)

# Strong refs to detached background flow-run tasks.
_BACKGROUND_TASKS: set[Any] = set()

_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def render_prompt(template: str, payload: dict[str, Any]) -> str:
    """Very small ``{{name}}`` substitution — no nested paths beyond dot.

    Missing keys are replaced with an empty string. Keeps the feature tiny
    without pulling in Jinja2.
    """
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        value: Any = payload
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        return "" if value is None else str(value)

    return _TEMPLATE_RE.sub(_sub, template or "")


async def get_or_404(
    session: AsyncSession, flow_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Flow:
    row = await FlowRepository(session).get(flow_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        raise NotFound("flow_not_found", code="flow.not_found")
    return row


async def create_flow(
    session: AsyncSession, *, workspace_id: uuid.UUID, created_by: uuid.UUID | None, **kwargs
) -> Flow:
    # Classic flows need an agent/squad target. Visual-DAG flows may have
    # ``graph_json`` with agent_call nodes that reference agents directly, so
    # ``agent_id``/``squad_id`` at the top level is optional.
    graph = kwargs.get("graph_json") or {}
    has_graph = isinstance(graph, dict) and isinstance(graph.get("nodes"), list) and len(graph["nodes"]) > 0
    if not has_graph and kwargs.get("agent_id") is None and kwargs.get("squad_id") is None:
        raise Conflict(
            "no_target", code="flow.no_target",
            extras={"hint": "Set agent_id, squad_id, or provide a visual graph."},
        )
    return await FlowRepository(session).create(
        workspace_id=workspace_id, created_by=created_by, **kwargs
    )


def _graph_is_active(graph: dict | None) -> bool:
    """Treat a graph as active if it has ≥1 node. Empty dict / empty nodes
    list = fall back to the legacy ``prompt_template`` path."""
    if not graph:
        return False
    nodes = graph.get("nodes")
    return isinstance(nodes, list) and len(nodes) > 0


async def trigger_flow(
    flow_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any] | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Fire a flow — returns the new FlowRun id.

    Picks one of two execution paths:

    * **Visual DAG** when ``flow.graph_json`` has nodes → ``flow_engine.run_graph()``.
      The engine creates its own per-node sub-sessions; the top-level FlowRun
      still gets a session so the UI can link back somewhere sensible.
    * **Classic** when ``graph_json`` is empty → the legacy single-prompt path
      (one ``agent_runner.run_agent_one_shot()`` call using ``prompt_template``).

    Opens its own DB session so this can be called from an APScheduler job.
    """
    factory = get_session_factory()
    async with factory() as db:
        flow = await FlowRepository(db).get(flow_id)
        if flow is None or flow.deleted_at is not None:
            raise NotFound("flow_not_found", code="flow.not_found")
        if not flow.enabled:
            raise Conflict("flow_disabled", code="flow.disabled")

        uses_graph = _graph_is_active(flow.graph_json)
        if not uses_graph and flow.agent_id is None:
            # Classic mode requires a single bound agent.
            raise Conflict(
                "squad_flow_not_supported", code="flow.squad_not_supported"
            )

        # Create a session for the run (classic mode uses it for the agent
        # turn; graph mode uses it as a symbolic anchor + session_message end
        # node target).
        new_session = await SessionRepository(db).create(
            workspace_id=flow.workspace_id,
            kind=SessionKind.P2P,
            subject_id=flow.agent_id,
            title=f"[flow] {flow.name}",
            metadata_json={"flow_id": str(flow.id), "trigger": trigger_kind},
        )
        fr: FlowRun = await FlowRunRepository(db).create(
            workspace_id=flow.workspace_id,
            flow_id=flow.id,
            session_id=new_session.id,
            trigger_kind=trigger_kind,
            trigger_payload_json=payload or {},
            status=FlowRunStatus.RUNNING,
            started_at=utcnow_naive(),
            triggered_by_identity_id=triggered_by,
        )
        await db.commit()

        run_id = fr.id
        ws_id = flow.workspace_id
        agent_id = flow.agent_id
        session_id = new_session.id
        graph = dict(flow.graph_json or {}) if uses_graph else None
        prompt = None if uses_graph else render_prompt(flow.prompt_template, payload or {})

    async def _execute() -> None:
        try:
            if graph is not None:
                # Visual DAG path.
                from app.services import flow_engine

                await flow_engine.run_graph(
                    flow_run_id=run_id,
                    graph=graph,
                    trigger_payload=payload or {},
                    workspace_id=ws_id,
                    session_id=session_id,
                    identity_id=triggered_by,
                )
            else:
                # Legacy single-prompt path (pre-D14).
                factory2 = get_session_factory()
                async with factory2() as db2:
                    result = await runner.run_agent_one_shot(
                        db2,
                        workspace_id=ws_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        identity_id=triggered_by,
                        user_text=prompt or "",
                    )
                    run_row = await FlowRunRepository(db2).get(run_id)
                    if run_row is not None:
                        run_row.status = (
                            FlowRunStatus.SUCCEEDED
                            if result.error is None
                            else FlowRunStatus.FAILED
                        )
                        run_row.finished_at = utcnow_naive()
                        run_row.output_summary = (result.final_text or "")[:1000]
                        run_row.error = result.error
                        await db2.flush([run_row])
                    await db2.commit()

            # Stamp last_run_at on the Flow regardless of mode.
            factory3 = get_session_factory()
            async with factory3() as db3:
                flow_row = await FlowRepository(db3).get(flow_id)
                if flow_row is not None:
                    flow_row.last_run_at = utcnow_naive()
                    await db3.flush([flow_row])
                    await db3.commit()
        except Exception as e:  # pragma: no cover
            log.exception("flow run failed")
            factory_err = get_session_factory()
            async with factory_err() as db_err:
                run_row = await FlowRunRepository(db_err).get(run_id)
                if run_row is not None:
                    run_row.status = FlowRunStatus.FAILED
                    run_row.finished_at = utcnow_naive()
                    run_row.error = str(e)[:1000]
                    await db_err.flush([run_row])
                    await db_err.commit()

    import asyncio

    task = asyncio.create_task(_execute())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return run_id
