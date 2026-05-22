"""M4.3 — Lineage replay service.

Resolves a compressed summary message back to the original turns it
folded so the chat trace UI can render an "Expand from summary"
drawer. The flow:

#. ``compaction_layer`` (sliding-window / manual / evolver) emits a
   summary :class:`~app.db.models.message.Message` and stamps two
   columns:

   * the summary's ``original_turns_ref`` records which turns it
     replaced, when, and which strategy emitted it;
   * each original turn's ``compressed_into_summary_id`` self-FKs
     to the summary so a forward read can also resolve "what
     summary absorbed this turn".

#. The runtime input pipeline keeps reading the *summary* row only —
   ``original_turns_ref`` is read-only side info that never enters
   the LLM context. The M0.7 cache prefix invariant therefore
   continues to hold across compactions: the prompt suffix is the
   summary message + tail, and the prefix (system prompt + memory)
   is unchanged.

#. The chat trace tab reads back through this service via the M4.3
   ``GET /sessions/{session_id}/messages/{message_id}/lineage``
   endpoint. Cross-workspace access is rejected at every entry by
   first resolving the parent session inside the caller's workspace.

The compaction layer wiring is **not part of M4.3** — the
sliding-window module ships with M3.x / M4.x compaction work. M4.3
delivers the schema, helper, service, endpoint, and frontend so a
follow-up compaction PR can land zero-friction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.message import (
    COMPACTION_STRATEGIES,
    LINEAGE_TEXT_EXCERPT_MAX_CHARS,
    Message,
)
from app.services import session as session_svc


# ─── Pure helpers ────────────────────────────────────────────
def _excerpt_for_message(msg: Message) -> str:
    """Render a 200-char preview of a message for the lineage drawer.

    Picks the first textual field present (``content_json.text`` →
    ``thinking_json.text`` → ``tool_call_json.name`` →
    ``tool_result_json.output``) and truncates with an ellipsis if
    over :data:`LINEAGE_TEXT_EXCERPT_MAX_CHARS`. The drawer is a
    debug surface, not a render of the full transcript — keeping
    excerpts short keeps the JSON payload bounded even when the
    summary covered 200 historical turns.
    """
    candidates: list[str | None] = []
    content = msg.content_json or {}
    if isinstance(content, dict):
        candidates.append(content.get("text") if isinstance(content.get("text"), str) else None)
    thinking = msg.thinking_json or {}
    if isinstance(thinking, dict):
        candidates.append(thinking.get("text") if isinstance(thinking.get("text"), str) else None)
    tool_call = msg.tool_call_json or {}
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        if isinstance(name, str):
            candidates.append(f"tool_call:{name}")
    tool_result = msg.tool_result_json or {}
    if isinstance(tool_result, dict):
        output = tool_result.get("output")
        if isinstance(output, str):
            candidates.append(output)
    text = next((c for c in candidates if c), "")
    if len(text) > LINEAGE_TEXT_EXCERPT_MAX_CHARS:
        return text[: LINEAGE_TEXT_EXCERPT_MAX_CHARS - 1] + "…"
    return text


def _coerce_strategy(value: Any) -> str:
    """Validate the ``compaction_strategy`` field on a stored ref.

    Returns ``"unknown"`` for anything not in
    :data:`COMPACTION_STRATEGIES`. Tolerant on read so a future
    strategy added without a back-fill doesn't 500 the endpoint.
    """
    if isinstance(value, str) and value in COMPACTION_STRATEGIES:
        return value
    return "unknown"


def _coerce_compressed_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.utcnow()


def _coerce_turn_ids(value: Any) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    if not isinstance(value, list):
        return out
    for raw in value:
        try:
            out.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    return out


# ─── DTOs ────────────────────────────────────────────────────
@dataclass(slots=True)
class LineageNode:
    message_id: uuid.UUID
    role: str
    text_excerpt: str
    created_at: datetime
    is_compressed_summary: bool
    is_original_turn: bool


@dataclass(slots=True)
class LineageReplay:
    summary_message_id: uuid.UUID
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    original_turn_count: int
    original_turns: list[LineageNode]
    compaction_strategy: str
    compressed_at: datetime


# ─── Compaction-layer helper ─────────────────────────────────
def mark_message_as_compressed(
    summary_message: Message,
    original_messages: Iterable[Message],
    *,
    strategy: str,
    compressed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the ``original_turns_ref`` payload for a summary row.

    Pure helper for the future compaction layer. Returns the dict the
    caller should assign to ``summary_message.original_turns_ref``;
    the caller is also responsible for setting each original message's
    ``compressed_into_summary_id`` to ``summary_message.id`` and
    flushing the session.

    Raises:
        ValueError: when ``strategy`` is not one of
            :data:`COMPACTION_STRATEGIES`. We validate at the helper
            so a typo at the call site can't sneak an unknown tag
            into the JSON.
    """
    if strategy not in COMPACTION_STRATEGIES:
        raise ValueError(
            f"unknown compaction_strategy {strategy!r}; "
            f"expected one of {sorted(COMPACTION_STRATEGIES)}"
        )
    originals = list(original_messages)
    when = (compressed_at or datetime.utcnow()).isoformat()
    return {
        "turn_message_ids": [str(m.id) for m in originals],
        "turn_count": len(originals),
        "compressed_at": when,
        "compaction_strategy": strategy,
    }


# ─── Read path ───────────────────────────────────────────────
async def get_lineage_replay(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
) -> LineageReplay | None:
    """Resolve a compressed summary message to its original turns.

    Returns ``None`` when ``message_id`` exists in the workspace but
    has no ``original_turns_ref`` — the caller (HTTP layer) maps that
    to a 404 ``lineage.not_compressed``. Cross-workspace access is
    refused by first loading the session through
    :func:`app.services.session.get_session_or_404`, which raises
    :class:`NotFound` with the same shape used elsewhere.
    """
    await session_svc.get_session_or_404(db, session_id, workspace_id=workspace_id)

    summary_stmt = select(Message).where(
        Message.id == message_id,
        Message.workspace_id == workspace_id,
        Message.session_id == session_id,
    )
    summary = (await db.execute(summary_stmt)).scalar_one_or_none()
    if summary is None:
        raise NotFound("message_not_found", code="message.not_found")

    ref = summary.original_turns_ref
    if not isinstance(ref, dict):
        return None

    turn_ids = _coerce_turn_ids(ref.get("turn_message_ids"))
    if not turn_ids:
        return LineageReplay(
            summary_message_id=summary.id,
            session_id=session_id,
            workspace_id=workspace_id,
            original_turn_count=0,
            original_turns=[],
            compaction_strategy=_coerce_strategy(ref.get("compaction_strategy")),
            compressed_at=_coerce_compressed_at(ref.get("compressed_at")),
        )

    originals_stmt = (
        select(Message)
        .where(
            Message.workspace_id == workspace_id,
            Message.id.in_(turn_ids),
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    originals = (await db.execute(originals_stmt)).scalars().all()

    nodes = [
        LineageNode(
            message_id=m.id,
            role=str(m.role),
            text_excerpt=_excerpt_for_message(m),
            created_at=m.created_at,
            is_compressed_summary=False,
            is_original_turn=True,
        )
        for m in originals
    ]
    return LineageReplay(
        summary_message_id=summary.id,
        session_id=session_id,
        workspace_id=workspace_id,
        original_turn_count=int(ref.get("turn_count") or len(nodes)),
        original_turns=nodes,
        compaction_strategy=_coerce_strategy(ref.get("compaction_strategy")),
        compressed_at=_coerce_compressed_at(ref.get("compressed_at")),
    )


async def list_compressed_summaries_in_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List every summary message in a session.

    Powers the chat trace tab badge ("compressed N original turns").
    Returns dicts (not the dataclass) so the API layer can pass them
    straight into the :class:`LineageSummary` Pydantic DTO. Capped at
    ``limit`` to keep the surface bounded for chat sessions that
    accumulate many sliding-window passes.
    """
    await session_svc.get_session_or_404(db, session_id, workspace_id=workspace_id)
    stmt = (
        select(Message)
        .where(
            Message.workspace_id == workspace_id,
            Message.session_id == session_id,
            Message.original_turns_ref.is_not(None),
        )
        .order_by(Message.created_at.asc())
        .limit(max(1, min(limit, 200)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for m in rows:
        ref = m.original_turns_ref or {}
        if not isinstance(ref, dict):
            continue
        out.append(
            {
                "summary_message_id": m.id,
                "role": str(m.role),
                "turn_count": int(ref.get("turn_count") or 0),
                "compaction_strategy": _coerce_strategy(ref.get("compaction_strategy")),
                "compressed_at": _coerce_compressed_at(ref.get("compressed_at")),
                "summary_excerpt": _excerpt_for_message(m),
            }
        )
    return out
