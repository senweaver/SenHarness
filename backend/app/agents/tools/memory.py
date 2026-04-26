"""Agent-facing memory tools: memorize / recall / forget / list_memories.

All tools operate against the current session's ``ToolRunContext`` and the
default workspace DB session. Scopes map as:

  user       → ``scope_id = identity_id``
  assistant  → ``scope_id = agent_id``
  workspace  → ``scope_id = None``
"""

from __future__ import annotations

import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.agents.tools._context import get_context
from app.db.models.memory import MemoryKind, MemoryScope
from app.db.session import get_session_factory
from app.services import memory as mem_svc


# ─── Shared helpers ──────────────────────────────────────
def _resolve_scope(
    scope: str, ctx
) -> tuple[MemoryScope, uuid.UUID | None]:
    s = scope.lower()
    if s == "user":
        return MemoryScope.USER, ctx.identity_id
    if s == "assistant":
        return MemoryScope.ASSISTANT, ctx.agent_id
    if s == "workspace":
        return MemoryScope.WORKSPACE, None
    return MemoryScope.USER, ctx.identity_id


# ─── memorize ────────────────────────────────────────────
class MemorizeArgs(BaseModel):
    """Memorize a fact. The text field accepts several common names (`content`,
    `value`, `text`, `fact`) — the LLM can use whichever feels natural."""

    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(
        ...,
        validation_alias=AliasChoices("content", "value", "text", "fact", "note"),
        description="The fact / preference to remember (free-form text).",
    )
    scope: str = Field(
        default="user",
        description="Who this memory is about. One of: 'user' (this human), "
        "'assistant' (this Agent), 'workspace' (whole workspace).",
    )
    kind: str = Field(
        default="semantic",
        description="'kv' for exact key-value facts, 'episodic' for time-stamped events, "
        "'semantic' (default) for free-form notes recallable by similarity.",
    )
    key: str | None = Field(
        default=None,
        description="Required when kind='kv'. Slug-like identifier (e.g. 'preferred_editor').",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_seconds: int | None = Field(
        default=None,
        description="Expire after N seconds. Omit for permanent memory.",
    )


async def run_memorize(args: MemorizeArgs) -> dict:
    ctx = get_context()
    scope_enum, scope_id = _resolve_scope(args.scope, ctx)
    kind_enum = MemoryKind(args.kind) if args.kind in {"kv", "episodic", "semantic"} else MemoryKind.SEMANTIC
    if kind_enum == MemoryKind.KV and not args.key:
        return {"ok": False, "error": "kv_memory_requires_key"}

    factory = get_session_factory()
    async with factory() as db:
        mem = await mem_svc.store(
            db,
            workspace_id=ctx.workspace_id,
            scope=scope_enum,
            scope_id=scope_id,
            kind=kind_enum,
            key=args.key,
            content=args.content,
            ttl_seconds=args.ttl_seconds,
            confidence=args.confidence,
            source_session_id=ctx.session_id,
            author_identity_id=ctx.identity_id,
        )
        await db.commit()
        return {
            "ok": True,
            "memory_id": str(mem.id),
            "scope": scope_enum.value,
            "kind": kind_enum.value,
            "key": mem.key,
        }


# ─── recall ──────────────────────────────────────────────
class RecallArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(
        default="",
        validation_alias=AliasChoices("query", "q", "text", "content", "topic"),
        description="Natural-language description of what to recall. If omitted, returns recent memories.",
    )
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity (0-1). Lower → more but noisier hits.",
    )


async def run_recall(args: RecallArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()

    # If the LLM didn't supply a query, fall back to listing recent memories so the
    # tool never returns empty-handed in the "just show me what you know" case.
    if not args.query.strip():
        from app.repositories.memory import MemoryRepository  # local import

        async with factory() as db:
            rows = await MemoryRepository(db).list_scoped(
                workspace_id=ctx.workspace_id,
                scope=None,
                scope_id=None,
                kind=None,
                limit=args.limit,
            )
        return {
            "query": "",
            "fallback": "no_query_listed_recent",
            "hits": [
                {
                    "id": str(m.id),
                    "scope": m.scope.value if hasattr(m.scope, "value") else str(m.scope),
                    "kind": m.kind.value if hasattr(m.kind, "value") else str(m.kind),
                    "key": m.key,
                    "content": m.content,
                    "score": None,
                    "confidence": m.confidence,
                }
                for m in rows
            ],
        }

    async with factory() as db:
        rows = await mem_svc.recall(
            db,
            workspace_id=ctx.workspace_id,
            identity_id=ctx.identity_id,
            agent_id=ctx.agent_id,
            query=args.query,
            limit=args.limit,
            min_score=args.min_score,
        )
    return {
        "query": args.query,
        "hits": [
            {
                "id": str(mem.id),
                "scope": mem.scope.value if hasattr(mem.scope, "value") else str(mem.scope),
                "kind": mem.kind.value if hasattr(mem.kind, "value") else str(mem.kind),
                "key": mem.key,
                "content": mem.content,
                "score": round(score, 3),
                "confidence": mem.confidence,
            }
            for mem, score in rows
        ],
    }


# ─── forget ──────────────────────────────────────────────
class ForgetArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    memory_id: str = Field(
        ...,
        validation_alias=AliasChoices("memory_id", "id"),
        description="The `memory_id` returned by memorize or recall.",
    )


async def run_forget(args: ForgetArgs) -> dict:
    ctx = get_context()
    try:
        mid = uuid.UUID(args.memory_id)
    except ValueError:
        return {"ok": False, "error": "invalid_memory_id"}
    factory = get_session_factory()
    async with factory() as db:
        try:
            await mem_svc.forget(db, workspace_id=ctx.workspace_id, memory_id=mid)
            await db.commit()
            return {"ok": True, "memory_id": args.memory_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ─── list_memories ───────────────────────────────────────
class ListMemoriesArgs(BaseModel):
    scope: str = Field(
        default="user",
        description="'user', 'assistant', or 'workspace'.",
    )
    kind: str | None = Field(default=None, description="Optional filter: 'kv'|'episodic'|'semantic'.")
    limit: int = Field(default=30, ge=1, le=200)


async def run_list_memories(args: ListMemoriesArgs) -> dict:
    from app.repositories.memory import MemoryRepository

    ctx = get_context()
    scope_enum, scope_id = _resolve_scope(args.scope, ctx)
    kind_enum = MemoryKind(args.kind) if args.kind in {"kv", "episodic", "semantic"} else None

    factory = get_session_factory()
    async with factory() as db:
        rows = await MemoryRepository(db).list_scoped(
            workspace_id=ctx.workspace_id,
            scope=scope_enum,
            scope_id=scope_id,
            kind=kind_enum,
            limit=args.limit,
        )
    return {
        "scope": scope_enum.value,
        "items": [
            {
                "id": str(mem.id),
                "kind": mem.kind.value if hasattr(mem.kind, "value") else str(mem.kind),
                "key": mem.key,
                "content": mem.content[:300],
                "confidence": mem.confidence,
                "updated_at": mem.updated_at.isoformat(),
            }
            for mem in rows
        ],
    }
