"""Memory service: store / recall / list / delete with embeddings."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.embedder import embed
from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.db.models.memory import Memory, MemoryKind, MemoryScope
from app.repositories.memory import MemoryRepository


async def store(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scope: MemoryScope,
    scope_id: uuid.UUID | None,
    kind: MemoryKind,
    content: str,
    key: str | None = None,
    value_json: dict | None = None,
    ttl_seconds: int | None = None,
    confidence: float = 1.0,
    source_session_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
    author_identity_id: uuid.UUID | None = None,
) -> Memory:
    """Idempotent for KV memories on (scope, scope_id, kind=kv, key).

    Embedding is computed from ``content`` automatically.
    """
    repo = MemoryRepository(session)

    # Upsert KV memories on (scope, scope_id, kind=kv, key).
    if kind == MemoryKind.KV and key:
        existing = await repo.get_kv(
            workspace_id=workspace_id,
            scope=scope,
            scope_id=scope_id,
            key=key,
        )
        if existing is not None:
            vec, model_tag = await embed(content)
            ttl_at = _ttl(ttl_seconds)
            await repo.update(
                existing,
                content=content,
                value_json=value_json or {},
                embedding=vec,
                embedding_model=model_tag,
                confidence=confidence,
                ttl_at=ttl_at,
                source_message_id=source_message_id,
                source_session_id=source_session_id,
                author_identity_id=author_identity_id,
            )
            return existing

    vec, model_tag = await embed(content)
    return await repo.create(
        workspace_id=workspace_id,
        scope=scope,
        scope_id=scope_id,
        kind=kind,
        key=key,
        content=content,
        value_json=value_json or {},
        embedding=vec,
        embedding_model=model_tag,
        confidence=confidence,
        ttl_at=_ttl(ttl_seconds),
        source_message_id=source_message_id,
        source_session_id=source_session_id,
        author_identity_id=author_identity_id,
    )


async def recall(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    query: str,
    limit: int = 6,
    min_score: float = 0.35,
) -> list[tuple[Memory, float]]:
    """Recall memories relevant to `query` across user/assistant/workspace scopes.

    Uses embedding cosine similarity. When the embedding backend is the dev
    hash-fallback (or any weak semantic embedder), also augments results with a
    simple token-overlap scorer so relevant memories still surface.
    """
    vec, _ = await embed(query)
    scopes: list[tuple[MemoryScope, uuid.UUID | None]] = [
        (MemoryScope.WORKSPACE, None),
    ]
    if identity_id is not None:
        scopes.append((MemoryScope.USER, identity_id))
    if agent_id is not None:
        scopes.append((MemoryScope.ASSISTANT, agent_id))

    repo = MemoryRepository(session)
    primary = await repo.recall_by_similarity(
        workspace_id=workspace_id,
        query_embedding=vec,
        scopes=scopes,
        limit=limit,
        min_score=min_score,
    )

    # Bolster with token-overlap scoring (always on) — helps especially with the
    # hash-embed dev fallback where cosine-similarity isn't reliable.
    if len(primary) < limit:
        got_ids = {m.id for m, _ in primary}
        fallback = await _token_overlap_recall(
            session,
            workspace_id=workspace_id,
            scopes=scopes,
            query=query,
            limit=limit - len(primary),
            exclude_ids=got_ids,
        )
        primary.extend(fallback)

    return primary[:limit]


async def _token_overlap_recall(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scopes: list[tuple[MemoryScope, uuid.UUID | None]],
    query: str,
    limit: int,
    exclude_ids: set,
) -> list[tuple[Memory, float]]:
    tokens = _tokenize(query)
    if not tokens:
        return []

    from sqlalchemy import or_, select

    scope_clauses = [
        (Memory.scope == s) & (Memory.scope_id.is_(None) if sid is None else Memory.scope_id == sid)
        for s, sid in scopes
    ]
    stmt = select(Memory).where(
        Memory.workspace_id == workspace_id,
        Memory.deleted_at.is_(None),
        or_(*scope_clauses),
    )
    rows = (await session.execute(stmt)).scalars().all()

    scored: list[tuple[Memory, float]] = []
    for mem in rows:
        if mem.id in exclude_ids:
            continue
        mem_tokens = _tokenize(mem.content) | _tokenize(mem.key or "")
        if not mem_tokens:
            continue
        overlap = len(tokens & mem_tokens)
        if overlap == 0:
            continue
        score = overlap / max(1, len(tokens))
        scored.append((mem, min(0.95, 0.4 + score * 0.5)))

    scored.sort(key=lambda p: p[1], reverse=True)
    return scored[:limit]


def _tokenize(text: str) -> set[str]:
    """Basic normalization: lowercase + split on non-alnum. Works for EN; for CN
    we also yield 2-grams so Chinese partial matches still score."""
    import re

    t = (text or "").lower().strip()
    if not t:
        return set()
    words = set(re.findall(r"[a-z0-9]+", t))
    # Chinese 2-grams
    cn = re.findall(r"[\u4e00-\u9fff]+", t)
    for run in cn:
        for i in range(len(run) - 1):
            words.add(run[i : i + 2])
        if len(run) == 1:
            words.add(run)
    # Drop extremely generic short tokens
    return {w for w in words if len(w) >= 2 or (len(w) == 1 and 0x4E00 <= ord(w[0]) <= 0x9FFF)}


async def forget(
    session: AsyncSession, *, workspace_id: uuid.UUID, memory_id: uuid.UUID
) -> None:
    repo = MemoryRepository(session)
    mem = await repo.get(memory_id)
    if mem is None or mem.workspace_id != workspace_id:
        raise NotFound("memory_not_found", code="memory.not_found")
    await repo.soft_delete(mem)


def _ttl(seconds: int | None) -> datetime | None:
    if not seconds:
        return None
    return utcnow_naive() + timedelta(seconds=seconds)
