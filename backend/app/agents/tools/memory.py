"""Agent-facing memory tools: memorize / recall / forget / list_memories.

All tools operate against the current session's ``ToolRunContext`` and the
default workspace DB session. Scopes map as:

  user       → ``scope_id = identity_id``
  assistant  → ``scope_id = agent_id``
  workspace  → ``scope_id = None``

M0.7 cache-aware mutation invariant
-----------------------------------

``memorize`` defaults to ``effective="next_session"``. The write lands
in ``pending_memories`` and is promoted at the end of the current run
(post-FINAL hook) so the system prompt that already lives in the
provider's prompt cache stays valid for the rest of this turn.
``effective="now"`` requires the workspace owner to have flipped
``home_config_json["memory"]["allow_immediate"] = True``; otherwise the
tool returns a ``rejected`` result with code
``memory.immediate_not_permitted`` so the agent receives clear feedback
without breaking the run.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.agents.tools._context import get_context
from app.core.errors import ImmediateMemoryNotPermitted, MemoryHardCapExceeded
from app.db.models.memory import MemoryKind, MemoryScope
from app.db.models.pending_memory import PendingMemoryTargetTable
from app.db.session import get_session_factory
from app.services import memory as mem_svc
from app.services import pending_memory as pending_memory_svc

log = logging.getLogger(__name__)


def _resolve_scope(scope: str, ctx) -> tuple[MemoryScope, uuid.UUID | None]:
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
    """Memorize a fact. ``effective`` defaults to ``next_session`` so the
    write doesn't break the prompt cache mid-turn.
    """

    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        validation_alias=AliasChoices("content", "value", "text", "fact", "note"),
        description="The fact / preference to remember (free-form text).",
    )
    scope: str = Field(
        default="user",
        description=(
            "Who this memory is about. One of: 'user' (this human), "
            "'assistant' (this Agent), 'workspace' (whole workspace)."
        ),
    )
    kind: str = Field(
        default="semantic",
        description=(
            "'kv' for exact key-value facts, 'episodic' for time-stamped events, "
            "'semantic' (default) for free-form notes recallable by similarity."
        ),
    )
    key: str | None = Field(
        default=None,
        max_length=200,
        description="Required when kind='kv'. Slug-like identifier (e.g. 'preferred_editor').",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ttl_seconds: int | None = Field(
        default=None,
        description="Expire after N seconds. Omit for permanent memory.",
    )
    effective: str = Field(
        default="next_session",
        description=(
            "When the write becomes visible to the model. Default "
            "'next_session' defers until the current run ends so prompt "
            "cache stays valid; 'now' applies immediately but requires "
            "the workspace to have opted in via memory.allow_immediate."
        ),
    )


async def run_memorize(args: MemorizeArgs) -> dict:
    ctx = get_context()
    scope_value = args.scope.lower() if args.scope else "user"
    if scope_value not in {"user", "assistant", "workspace"}:
        scope_value = "user"
    kind_value = args.kind.lower() if args.kind else "semantic"
    if kind_value not in {"kv", "episodic", "semantic"}:
        kind_value = "semantic"
    if kind_value == "kv" and not args.key:
        return {"status": "rejected", "code": "memory.kv_requires_key"}

    effective = args.effective if args.effective in {"next_session", "now"} else "next_session"
    payload = {
        "content": args.content,
        "scope": scope_value,
        "kind": kind_value,
        "key": args.key,
        "confidence": float(args.confidence),
        "ttl_seconds": args.ttl_seconds,
        "source_session_id": str(ctx.session_id) if ctx.session_id else None,
    }

    factory = get_session_factory()
    async with factory() as db:
        try:
            pending, applied = await pending_memory_svc.queue_immediate_or_pending(
                db,
                workspace_id=ctx.workspace_id,
                session_id=ctx.session_id,
                identity_id=ctx.identity_id,
                agent_id=ctx.agent_id,
                target_table=PendingMemoryTargetTable.MEMORIES,
                payload=payload,
                effective=effective,  # type: ignore[arg-type]
            )
        except ImmediateMemoryNotPermitted:
            await db.commit()
            return {
                "status": "rejected",
                "code": "memory.immediate_not_permitted",
                "message": (
                    "Workspace policy disables 'effective=now'. Use the "
                    "default 'next_session' so the write applies on the "
                    "next run boundary."
                ),
            }
        except MemoryHardCapExceeded as exc:
            await db.commit()
            return {
                "status": "rejected",
                "code": "memory.hard_cap_exceeded",
                "message": (
                    "This memory would push the always-on memory bucket "
                    "past the workspace's hard cap; ask a workspace admin "
                    "to consolidate or raise memory.always_on_max_chars."
                ),
                "extras": getattr(exc, "extras", {}),
            }
        await db.commit()

    if pending is not None:
        return {
            "status": "deferred",
            "pending_memory_id": str(pending.id),
            "effective": "next_session",
            "scope": scope_value,
            "kind": kind_value,
            "key": args.key,
            "note": (
                "Memory will be applied at the end of the current run for prompt-cache safety."
            ),
        }
    applied_record = applied or {}
    return {
        "status": "applied",
        "memory_id": applied_record.get("id"),
        "scope": applied_record.get("scope") or scope_value,
        "kind": applied_record.get("kind") or kind_value,
        "key": applied_record.get("key") or args.key,
        "effective": "now",
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

    if not args.query.strip():
        from app.repositories.memory import MemoryRepository

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
        try:
            rows = await mem_svc.recall(
                db,
                workspace_id=ctx.workspace_id,
                identity_id=ctx.identity_id,
                agent_id=ctx.agent_id,
                query=args.query,
                limit=args.limit,
                min_score=args.min_score,
            )
        except Exception:
            log.exception(
                "memory.recall failed workspace=%s query_len=%d",
                ctx.workspace_id,
                len((args.query or "").strip()),
            )
            return {
                "query": args.query,
                "status": "error",
                "code": "memory.recall_failed",
                "hits": [],
                "message": (
                    "Recall could not run — embeddings may be unavailable "
                    "in this workspace. Try listing recent memories instead."
                ),
            }
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
    kind: str | None = Field(
        default=None, description="Optional filter: 'kv'|'episodic'|'semantic'."
    )
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
