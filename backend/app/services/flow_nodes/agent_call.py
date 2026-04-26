"""``agent_call`` node — run an Agent with a templated prompt.

``data`` shape::

    {
        "agent_id": "<uuid>",            # required
        "prompt_template": "...",        # uses {{start.x}}, {{n2.text}}
        "iteration_budget": 8            # optional, 1-32
    }

Output::

    {
        "text":      "<final answer>",
        "session_id": "<uuid>",
        "tokens":    {"input": int, "output": int},
        "cost":      <float usd>,
        "tool_events": [...]
    }
"""

from __future__ import annotations

import uuid

from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.session import SessionRepository
from app.services import agent_runner as runner
from app.services.flow_nodes import NodeContext


async def run_agent_call(ctx: NodeContext) -> dict:
    agent_id_raw = ctx.data.get("agent_id")
    if not agent_id_raw:
        raise ValueError("agent_call.data.agent_id is required")
    try:
        agent_id = uuid.UUID(str(agent_id_raw))
    except (ValueError, TypeError) as e:
        raise ValueError(f"invalid agent_id: {agent_id_raw!r}") from e

    prompt = ctx.render_str(ctx.data.get("prompt_template")) or ""
    if not prompt.strip():
        raise ValueError(
            "agent_call produced an empty prompt — check your template "
            "and upstream node outputs"
        )

    iteration_budget = int(ctx.data.get("iteration_budget") or 8)
    iteration_budget = max(1, min(iteration_budget, 32))

    factory = get_session_factory()
    async with factory() as db:
        # Create a fresh P2P session for this node call so messages + usage
        # are inspectable from the normal chat UI afterwards.
        session_row = await SessionRepository(db).create(
            workspace_id=ctx.workspace_id,
            kind=SessionKind.P2P,
            subject_id=agent_id,
            owner_identity_id=ctx.identity_id,
            title=f"[flow:{ctx.node_id}]",
            metadata_json={
                "flow_node_id": ctx.node_id,
                "flow_trigger_payload": ctx.trigger_payload,
            },
        )
        new_session_id = session_row.id
        await db.commit()

        result = await runner.run_agent_one_shot(
            db,
            workspace_id=ctx.workspace_id,
            agent_id=agent_id,
            session_id=new_session_id,
            identity_id=ctx.identity_id,
            user_text=prompt,
            iteration_budget=iteration_budget,
        )
        await db.commit()

    if result.error:
        raise RuntimeError(result.error)

    tokens = (result.usage_payload.get("tokens") or {})
    return {
        "text": result.final_text,
        "session_id": str(new_session_id),
        "tokens": {
            "input": int(tokens.get("input") or 0),
            "output": int(tokens.get("output") or 0),
        },
        "cost": float(result.usage_payload.get("cost") or 0.0),
        "tool_events": result.tool_events,
    }
