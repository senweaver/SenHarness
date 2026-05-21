"""Flow node runner registry.

Each runner is ``async def runner(ctx: NodeContext) -> dict``. The dict gets
stored in the graph context under the node id, so downstream nodes can
reference any field via ``{{<node_id>.<field>}}`` templates.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class NodeContext:
    """Runtime context passed to every node runner."""

    node_id: str
    node_type: str
    data: dict[str, Any]
    context: dict[str, Any]  # accumulated {<node_id>: output}
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None
    identity_id: uuid.UUID | None
    trigger_payload: dict[str, Any]
    # Callable to render a template against the current context.
    render: Callable[[str, dict[str, Any]], str]

    def render_str(self, template: str | None) -> str:
        if not template:
            return ""
        return self.render(template, self.context)


NodeRunner = Callable[[NodeContext], Awaitable[dict[str, Any]]]


from app.services.flow_nodes.agent_call import run_agent_call  # noqa: E402
from app.services.flow_nodes.end import run_end  # noqa: E402
from app.services.flow_nodes.http_request import run_http_request  # noqa: E402
from app.services.flow_nodes.start import run_start  # noqa: E402

NODE_RUNNERS: dict[str, NodeRunner] = {
    "start": run_start,
    "agent_call": run_agent_call,
    "http_request": run_http_request,
    "end": run_end,
}


__all__ = ["NODE_RUNNERS", "NodeContext", "NodeRunner"]
