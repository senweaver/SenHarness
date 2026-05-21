"""``end`` node — surface a result on the FlowRun row + optional side effects.

``data`` shape::

    {
        "output_mode": "flow_run" | "session_message" | "noop",
        "text":        "{{n2.text}}"   # templated, max 4000 chars
    }

* ``flow_run`` (default) — just records ``text`` as ``FlowRun.output_summary``.
* ``session_message`` — appends an assistant message with the text to the
  Flow's session (requires the flow.session_id to exist; falls back silently
  otherwise).
* ``noop`` — no side effects (just acts as a DAG terminator).

Output::

    {"text": "<rendered text>"}
"""

from __future__ import annotations

from app.db.models.message import MessageRole
from app.db.session import get_session_factory
from app.repositories.session import SessionRepository
from app.services import session as sess_svc
from app.services.flow_nodes import NodeContext


async def run_end(ctx: NodeContext) -> dict:
    mode = str(ctx.data.get("output_mode") or "flow_run")
    text = ctx.render_str(ctx.data.get("text"))[:4000]

    if mode == "session_message" and ctx.session_id is not None:
        factory = get_session_factory()
        async with factory() as db:
            sess = await SessionRepository(db).get(ctx.session_id)
            if sess is not None and sess.workspace_id == ctx.workspace_id:
                await sess_svc.append_message(
                    db,
                    session_obj=sess,
                    role=MessageRole.ASSISTANT,
                    content_json={"text": text},
                )
                await db.commit()

    return {"text": text, "mode": mode}
