"""Memory repository — pgvector cosine similarity recall."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import desc, select

from app.db.models.memory import Memory, MemoryKind, MemoryScope
from app.db.repository import AsyncRepository


class MemoryRepository(AsyncRepository[Memory]):
    model = Memory

    async def recall_by_similarity(
        self,
        *,
        workspace_id: uuid.UUID,
        query_embedding: list[float],
        scopes: list[tuple[MemoryScope, uuid.UUID | None]],
        limit: int = 6,
        min_score: float = 0.35,
    ) -> list[tuple[Memory, float]]:
        """Top-k memories by cosine similarity scoped to (scope, scope_id) pairs."""
        if not scopes:
            return []
        scope_clauses = [
            (Memory.scope == s)
            & (Memory.scope_id.is_(None) if sid is None else Memory.scope_id == sid)
            for s, sid in scopes
        ]
        from sqlalchemy import or_

        stmt = (
            select(Memory, Memory.embedding.cosine_distance(query_embedding).label("dist"))
            .where(
                Memory.workspace_id == workspace_id,
                Memory.deleted_at.is_(None),
                Memory.embedding.is_not(None),
                or_(*scope_clauses),
            )
            .order_by("dist")
            .limit(limit * 2)  # over-fetch, filter by score below
        )
        rows = (await self.session.execute(stmt)).all()
        out: list[tuple[Memory, float]] = []
        for mem, dist in rows:
            score = 1.0 - float(dist)
            if score >= min_score:
                out.append((mem, score))
            if len(out) >= limit:
                break
        return out

    async def list_scoped(
        self,
        *,
        workspace_id: uuid.UUID,
        scope: MemoryScope | None = None,
        scope_id: uuid.UUID | None = None,
        kind: MemoryKind | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Memory]:
        stmt = select(Memory).where(
            Memory.workspace_id == workspace_id,
            Memory.deleted_at.is_(None),
        )
        if scope is not None:
            stmt = stmt.where(Memory.scope == scope)
        if scope_id is not None:
            stmt = stmt.where(Memory.scope_id == scope_id)
        if kind is not None:
            stmt = stmt.where(Memory.kind == kind)
        if q:
            like = f"%{q.strip()}%"
            # Content ILIKE covers body text; key ILIKE lets users jump to a
            # specific kv entry.
            from sqlalchemy import or_

            stmt = stmt.where(or_(Memory.content.ilike(like), Memory.key.ilike(like)))
        stmt = stmt.order_by(desc(Memory.updated_at)).offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def stats(self, *, workspace_id: uuid.UUID) -> dict[str, dict[str, int]]:
        """Return ``{"by_scope": {...}, "by_kind": {...}, "total": int}``."""
        from sqlalchemy import func

        cond = (
            Memory.workspace_id == workspace_id,
            Memory.deleted_at.is_(None),
        )
        scope_stmt = select(Memory.scope, func.count(Memory.id)).where(*cond).group_by(Memory.scope)
        kind_stmt = select(Memory.kind, func.count(Memory.id)).where(*cond).group_by(Memory.kind)
        total_stmt = select(func.count(Memory.id)).where(*cond)

        by_scope = {
            str(r[0]): int(r[1] or 0) for r in (await self.session.execute(scope_stmt)).all()
        }
        by_kind = {str(r[0]): int(r[1] or 0) for r in (await self.session.execute(kind_stmt)).all()}
        total = int((await self.session.execute(total_stmt)).scalar() or 0)
        return {"by_scope": by_scope, "by_kind": by_kind, "total": total}

    async def get_kv(
        self,
        *,
        workspace_id: uuid.UUID,
        scope: MemoryScope,
        scope_id: uuid.UUID | None,
        key: str,
    ) -> Memory | None:
        stmt = select(Memory).where(
            Memory.workspace_id == workspace_id,
            Memory.scope == scope,
            Memory.kind == MemoryKind.KV,
            Memory.key == key,
            Memory.deleted_at.is_(None),
        )
        if scope_id is None:
            stmt = stmt.where(Memory.scope_id.is_(None))
        else:
            stmt = stmt.where(Memory.scope_id == scope_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def purge_expired(self, *, now: datetime) -> int:
        stmt = select(Memory).where(
            Memory.ttl_at.is_not(None), Memory.ttl_at < now, Memory.deleted_at.is_(None)
        )
        expired = (await self.session.execute(stmt)).scalars().all()
        for mem in expired:
            await self.soft_delete(mem)
        return len(expired)
