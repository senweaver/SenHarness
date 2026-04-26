"""``start`` node — echoes the trigger payload as output.

A Flow must have exactly one start node; the visual editor inserts it
automatically when the canvas is empty. Everything downstream references
``{{start.<field>}}`` to read the trigger payload.
"""

from __future__ import annotations

from app.services.flow_nodes import NodeContext


async def run_start(ctx: NodeContext) -> dict:
    payload = ctx.trigger_payload or {}
    # Expose both the flat payload (for {{start.<field>}}) and a ``payload``
    # sub-key (for templates that prefer the verbose form).
    return {**payload, "payload": payload}
