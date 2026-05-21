"""knowledge_search tool — agent-callable RAG retrieval.

The tool operates in the caller's workspace. It accepts:
    * ``collection``: collection NAME (case-insensitive) OR UUID.
    * ``query``: natural-language query.
    * ``top_k``: 1-10.

Returns a list of ``{doc_title, ord, text, score}`` hits. The agent should
quote or paraphrase with citations back to the user.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.tools._context import get_context
from app.db.models.knowledge import KnowledgeCollection
from app.db.session import get_session_factory
from app.services import knowledge as svc


class KnowledgeSearchArgs(BaseModel):
    collection: str = Field(
        description="Collection name (case-insensitive) or UUID.",
        min_length=1,
        max_length=128,
    )
    query: str = Field(min_length=1, max_length=1024)
    top_k: int = Field(default=5, ge=1, le=10)


async def run_knowledge_search(args: KnowledgeSearchArgs) -> dict:
    ctx = get_context()
    workspace_id = ctx.workspace_id

    factory = get_session_factory()
    async with factory() as db:
        col = await _resolve_collection(db, workspace_id, args.collection)
        if col is None:
            return {
                "ok": False,
                "error": f"collection_not_found: {args.collection!r}",
                "hits": [],
            }
        hits = await svc.search(
            db, collection=col, query=args.query, top_k=args.top_k
        )

    return {
        "ok": True,
        "collection_id": str(col.id),
        "collection_name": col.name,
        "hits": [
            {
                "doc_title": h.doc_title,
                "ord": h.ord,
                "text": h.text,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


async def _resolve_collection(
    db, workspace_id: uuid.UUID, selector: str
) -> KnowledgeCollection | None:
    """Accept either a UUID or a (case-insensitive) name."""
    try:
        cid = uuid.UUID(selector)
        stmt = select(KnowledgeCollection).where(
            KnowledgeCollection.id == cid,
            KnowledgeCollection.workspace_id == workspace_id,
            KnowledgeCollection.deleted_at.is_(None),
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
    except ValueError:
        pass
    stmt = select(KnowledgeCollection).where(
        KnowledgeCollection.workspace_id == workspace_id,
        KnowledgeCollection.deleted_at.is_(None),
        func.lower(KnowledgeCollection.name) == selector.strip().lower(),
    )
    return (await db.execute(stmt)).scalar_one_or_none()
