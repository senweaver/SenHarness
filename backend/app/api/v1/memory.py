"""Memory routes — CRUD + semantic recall."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.db.models.memory import MemoryKind, MemoryScope
from app.repositories.memory import MemoryRepository
from app.schemas.memory import (
    MemoryCreate,
    MemoryRead,
    MemoryUpdate,
    RecallHit,
    RecallIn,
)
from app.services import memory as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    scope: MemoryScope | None = None,
    scope_id: uuid.UUID | None = None,
    kind: MemoryKind | None = None,
    q: str | None = Query(None, description="ILIKE match on content + key."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[MemoryRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await MemoryRepository(db).list_scoped(
        workspace_id=ws_id,
        scope=scope,
        scope_id=scope_id,
        kind=kind,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [MemoryRead.model_validate(m) for m in rows]


@router.get("/stats")
async def memory_stats(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    return await MemoryRepository(db).stats(workspace_id=ws_id)


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MemoryRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)

    scope_id = body.scope_id
    if body.scope == MemoryScope.USER and scope_id is None:
        scope_id = identity_id

    mem = await svc.store(
        db,
        workspace_id=ws_id,
        scope=body.scope,
        scope_id=scope_id,
        kind=body.kind,
        key=body.key,
        content=body.content,
        value_json=body.value_json,
        confidence=body.confidence,
        ttl_seconds=body.ttl_seconds,
        author_identity_id=identity_id,
    )
    await db.commit()
    return MemoryRead.model_validate(mem)


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MemoryRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    repo = MemoryRepository(db)
    mem = await repo.get(memory_id)
    if mem is None or mem.workspace_id != ws_id:
        raise NotFound("memory_not_found", code="memory.not_found")

    # If `content` changes, re-embed.
    if body.content and body.content != mem.content:
        from app.agents.harness.embedder import embed  # local import

        vec, model_tag = await embed(body.content)
        await repo.update(mem, content=body.content, embedding=vec, embedding_model=model_tag)
    if body.value_json is not None:
        await repo.update(mem, value_json=body.value_json)
    if body.confidence is not None:
        await repo.update(mem, confidence=body.confidence)
    await db.commit()
    return MemoryRead.model_validate(mem)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.forget(db, workspace_id=ws_id, memory_id=memory_id)
    await db.commit()


@router.post("/recall", response_model=list[RecallHit])
async def recall_memory(
    body: RecallIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[RecallHit]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.recall(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        agent_id=None,
        query=body.query,
        limit=body.limit,
        min_score=body.min_score,
    )
    return [
        RecallHit(memory=MemoryRead.model_validate(m), score=s) for m, s in rows
    ]
