"""Runtime memory injection — assembles the four-layer memory fragment
the runner folds into every system prompt:

1. **L1 · semantic profiles** — workspace MEMORY.md + identity USER.md +
   identity SOUL.md (see :mod:`app.services.memory_profile`). Capped
   per-kind so injection stays cheap.
2. **L4 · embedding-recalled notes** — the top-k memories closest to the
   current user message (via :func:`app.services.memory.recall`).
3. **L4 · 12-dim user facts (M3.7)** — the per-identity dialectic user
   model produced by :mod:`app.services.user_profile`. Sits *after* the
   semantic profile so the more concrete bullets read first; sized
   under the M0.7 always-on hard cap (4000 chars) on its own so a
   noisy extractor never starves the recalled-notes block.

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
from app.services import user_profile as user_profile_svc

log = logging.getLogger(__name__)

MAX_MEMORIES = 6
MAX_CONTENT_CHARS = 400
# Short queries (greetings, confirmations like "你是" / "hi") never recall
# anything useful but still pay one embedder round-trip (~3s on remote
# providers).  Skip the recall step below this stripped-length threshold.
RECALL_MIN_QUERY_CHARS = 4


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
    user_facts_fragment: str = ""

    try:
        async with factory() as db:
            profile_fragment = await profile_svc.load_profile_fragment(
                db,
                workspace_id=workspace_id,
                identity_id=identity_id,
            )
            stripped_query = (user_text or "").strip()
            if len(stripped_query) >= RECALL_MIN_QUERY_CHARS:
                recall_rows = await mem_svc.recall(
                    db,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                    agent_id=agent_id,
                    query=stripped_query,
                    limit=MAX_MEMORIES,
                    min_score=0.30,
                )
            if identity_id is not None:
                user_facts_fragment = await user_profile_svc.render_facts_for_injection(
                    db,
                    workspace_id=workspace_id,
                    identity_id=identity_id,
                    max_chars=user_profile_svc.DEFAULT_INJECT_MAX_CHARS,
                )
    except Exception as e:  # pragma: no cover
        log.debug("memory fetch failed: %s", e)

    parts: list[str] = []
    if profile_fragment:
        parts.append(profile_fragment)

    if user_facts_fragment:
        parts.append(user_facts_fragment)

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
            "not the user's current message.\n" + "\n".join(lines)
        )

    return "\n\n".join(parts) if parts else None
