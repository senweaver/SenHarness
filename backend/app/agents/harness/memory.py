"""Runtime memory injection — assembles the four-layer memory fragment
the runner folds into every system prompt:

1. **L1 · semantic profiles** — workspace MEMORY.md + identity USER.md +
   identity SOUL.md (see :mod:`app.services.memory_profile`). Capped
   per-kind so injection stays cheap.
2. **L4 · embedding-recalled notes** — the top-k memories closest to the
   current user message (via :func:`app.services.memory.recall`).

L2 episodic memory (full-text over past messages) is exposed as the
``session_search`` *tool* rather than auto-injected so the Agent pulls
it on demand; L3 procedural memory lives in the skills system.
"""

from __future__ import annotations

import logging
import uuid

from app.db.session import get_session_factory
from app.services import memory as mem_svc
from app.services import memory_profile as profile_svc

log = logging.getLogger(__name__)

MAX_MEMORIES = 6
MAX_CONTENT_CHARS = 400


async def fetch_system_memory_fragment(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_text: str,
) -> str | None:
    """Return the composed memory fragment — profiles + recalled notes, or
    ``None`` when neither is populated."""
    factory = get_session_factory()
    profile_fragment: str | None = None
    recall_rows: list = []

    try:
        async with factory() as db:
            profile_fragment = await profile_svc.load_profile_fragment(
                db,
                workspace_id=workspace_id,
                identity_id=identity_id,
            )
            recall_rows = await mem_svc.recall(
                db,
                workspace_id=workspace_id,
                identity_id=identity_id,
                agent_id=agent_id,
                query=user_text,
                limit=MAX_MEMORIES,
                min_score=0.30,
            )
    except Exception as e:  # pragma: no cover
        log.debug("memory fetch failed: %s", e)

    parts: list[str] = []
    if profile_fragment:
        parts.append(profile_fragment)

    if recall_rows:
        lines: list[str] = []
        for mem, score in recall_rows:
            scope_s = mem.scope.value if hasattr(mem.scope, "value") else str(mem.scope)
            kind_s = mem.kind.value if hasattr(mem.kind, "value") else str(mem.kind)
            key_s = f"[{mem.key}] " if mem.key else ""
            body = (mem.content or "").strip().replace("\n", " ")
            if len(body) > MAX_CONTENT_CHARS:
                body = body[:MAX_CONTENT_CHARS] + "…"
            lines.append(f"- ({scope_s}·{kind_s}, {score:.2f}) {key_s}{body}")
        parts.append(
            "## RECALLED NOTES\n"
            "Use what's relevant, ignore the rest; these reflect past conversations, "
            "not the user's current message.\n"
            + "\n".join(lines)
        )

    return "\n\n".join(parts) if parts else None
