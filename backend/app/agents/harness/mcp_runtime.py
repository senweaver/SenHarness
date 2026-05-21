"""M2.5.4 — MEDIA-aware bridge between MCP tool results and RunEvent frames.

The native runner already emits ``TOOL_RESULT`` frames inside its
``agent.iter()`` loop; M2.5.3's failover work owns that block, so
this milestone deliberately *does not* touch it. Instead we expose
a single helper that any future MCP capability (or the runner once
M2.5.5's plugin host lands) can call to translate an
:class:`McpToolResult` into the wire payload the WS layer already
understands.

The wire format:

```python
RunEvent(
    kind=RunEventKind.TOOL_RESULT,
    data={
        "id": call_id,
        "name": tool_name,
        "result": result.text,
        "is_error": result.is_error,
        "media": [
            {"type": "image", "mime": "image/png", "data": "b64..."},
            {"type": "audio", "mime": "audio/wav", "url": null, "data": "..."},
            {"type": "file",  "mime": "application/pdf", "url": "..."},
        ],
    },
)
```

Frontend consumers branch on ``media[*].type`` — ``image`` /
``audio`` / ``file`` — and fall back to the text envelope when the
list is empty (which is the legacy non-media path for non-MCP
tools).
"""

from __future__ import annotations

from typing import Any

from app.agents.kernels.base import RunEvent, RunEventKind
from app.services.mcp_client import McpMediaPart, McpToolResult


def media_part_to_wire(part: McpMediaPart) -> dict[str, Any]:
    """Render a single media part as a JSON-safe dict.

    Keeps the field set small and stable so the frontend does not
    have to defend against extra keys: ``type``, ``mime``, plus
    *one of* ``url`` / ``data`` / ``filename``. When the SDK gave us
    nothing useful, the dict still has ``type`` + ``mime`` so the UI
    can render an "unknown attachment" fallback.
    """
    payload: dict[str, Any] = {"type": part.kind}
    if part.mime:
        payload["mime"] = part.mime
    if part.url:
        payload["url"] = part.url
    if part.data_b64:
        payload["data"] = part.data_b64
    if part.filename:
        payload["filename"] = part.filename
    return payload


def tool_result_to_event_data(
    *,
    call_id: str,
    tool_name: str,
    result: McpToolResult,
) -> dict[str, Any]:
    """Compose the ``RunEvent.data`` dict for a TOOL_RESULT frame.

    The ``media`` key is always included (empty list when the tool
    returned text only) so consumers can rely on its shape. Callers
    pass the resulting dict straight to ``RunEvent(kind=TOOL_RESULT,
    data=...)``.
    """
    return {
        "id": call_id,
        "name": tool_name,
        "result": result.text,
        "is_error": result.is_error,
        "media": [media_part_to_wire(p) for p in result.media],
    }


def build_mcp_tool_result_event(
    *,
    call_id: str,
    tool_name: str,
    result: McpToolResult,
) -> RunEvent:
    """One-shot helper: build the RunEvent that the WS layer forwards."""
    return RunEvent(
        kind=RunEventKind.TOOL_RESULT,
        data=tool_result_to_event_data(
            call_id=call_id,
            tool_name=tool_name,
            result=result,
        ),
    )


__all__ = [
    "build_mcp_tool_result_event",
    "media_part_to_wire",
    "tool_result_to_event_data",
]
