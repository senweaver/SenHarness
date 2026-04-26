"""Unit tests for kb_source service pieces that don't need a DB session."""

from __future__ import annotations

import json

from app.services.kb_source import SyncUpdate, stream_sse


async def _iter(items):
    for i in items:
        yield i


async def test_stream_sse_serializes_update_frames():
    updates = [
        SyncUpdate(kind="started", payload={"sync_id": "abc"}),
        SyncUpdate(kind="progress", payload={"level": "info", "msg": "hi"}),
        SyncUpdate(
            kind="done",
            payload={"status": "succeeded", "docs_added": 1, "error": None},
        ),
    ]
    frames: list[bytes] = []
    async for frame in stream_sse(_iter(updates)):
        frames.append(frame)
    assert frames, "stream should emit at least the three updates"
    # First frame must be event: started + a JSON body
    first = frames[0].decode()
    assert first.startswith("event: started\n")
    body_line = [ln for ln in first.splitlines() if ln.startswith("data: ")][0]
    doc = json.loads(body_line[len("data: ") :])
    assert doc == {"kind": "started", "sync_id": "abc"}

    # Last frame must be the "done" event
    last = frames[-1].decode()
    assert last.startswith("event: done\n")
