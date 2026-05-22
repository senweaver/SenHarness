"""Knowledge collection + document CRUD + search endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.db.repository import AsyncRepository
from app.schemas.knowledge import (
    AttachmentIngestIn,
    DocIngestIn,
    KnowledgeChunkHit,
    KnowledgeCollectionCard,
    KnowledgeCollectionCreate,
    KnowledgeCollectionRead,
    KnowledgeCollectionUpdate,
    KnowledgeDocRead,
    KnowledgeSearchIn,
)
from app.services import attachment as att_svc
from app.services import audit as audit_svc
from app.services import kb_source as kb_src_svc
from app.services import knowledge as svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Collections ──────────────────────────────────────────
@router.get("/collections", response_model=list[KnowledgeCollectionCard])
async def list_collections(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[KnowledgeCollectionCard]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_collections(db, workspace_id=ws_id)
    out: list[KnowledgeCollectionCard] = []
    for col, doc_count, chunk_count in rows:
        card = KnowledgeCollectionCard.model_validate(col)
        card.doc_count = doc_count
        card.chunk_count = chunk_count
        out.append(card)
    return out


@router.post(
    "/collections",
    response_model=KnowledgeCollectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    body: KnowledgeCollectionCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> KnowledgeCollectionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    from app.db.models.knowledge import KnowledgeCollection

    col = await AsyncRepository(db, KnowledgeCollection).create(
        workspace_id=ws_id,
        name=body.name,
        description=body.description,
        config_json=body.config_json,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="knowledge.collection.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="collection",
        resource_id=col.id,
        summary=f"created knowledge collection {col.name!r}",
        request=request,
    )
    await db.commit()
    return KnowledgeCollectionRead.model_validate(col)


@router.patch("/collections/{collection_id}", response_model=KnowledgeCollectionRead)
async def update_collection(
    collection_id: uuid.UUID,
    body: KnowledgeCollectionUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> KnowledgeCollectionRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    from app.db.models.knowledge import KnowledgeCollection

    col = await AsyncRepository(db, KnowledgeCollection).update(
        col, **body.model_dump(exclude_none=True)
    )
    await db.commit()
    return KnowledgeCollectionRead.model_validate(col)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    from app.db.models.knowledge import KnowledgeCollection

    await AsyncRepository(db, KnowledgeCollection).soft_delete(col)
    await audit_svc.record(
        db,
        action="knowledge.collection.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="collection",
        resource_id=col.id,
        summary=f"deleted collection {col.name!r}",
        request=request,
    )
    await db.commit()


# ─── Documents ────────────────────────────────────────────
@router.get(
    "/collections/{collection_id}/docs",
    response_model=list[KnowledgeDocRead],
)
async def list_docs(
    collection_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[KnowledgeDocRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    from sqlalchemy import desc, select

    stmt = (
        select(KnowledgeDoc)
        .where(
            KnowledgeDoc.collection_id == col.id,
            KnowledgeDoc.deleted_at.is_(None),
        )
        .order_by(desc(KnowledgeDoc.created_at))
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [KnowledgeDocRead.model_validate(r) for r in rows]


@router.post(
    "/collections/{collection_id}/docs",
    response_model=KnowledgeDocRead,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_doc(
    collection_id: uuid.UUID,
    body: DocIngestIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> KnowledgeDocRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    result = await svc.ingest_document(
        db,
        collection=col,
        title=body.title,
        source_kind=body.source_kind,
        source_uri=body.source_uri,
        raw_text=body.raw_text,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="knowledge.doc.ingest",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="doc",
        resource_id=result.doc.id,
        summary=f"ingested {body.title!r} → {result.chunks} chunks",
        metadata={"source_kind": body.source_kind, "chunks": result.chunks},
        request=request,
    )
    await db.commit()
    return KnowledgeDocRead.model_validate(result.doc)


@router.post(
    "/collections/{collection_id}/ingest_attachment",
    response_model=KnowledgeDocRead,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_from_attachment(
    collection_id: uuid.UUID,
    body: AttachmentIngestIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> KnowledgeDocRead:
    """One-click import of an existing chat attachment into a collection.

    The attachment must belong to the caller's active workspace (enforced by
    ``attachment.get_for_read``). Supported mime types are textual files and
    PDFs (via ``pypdf`` if installed); audio / video / images are rejected
    with a machine-readable error code so the UI can tell the user *why*.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    # Load the attachment (workspace-scoped) + read bytes off disk.
    att = await att_svc.get_for_read(db, attachment_id=body.attachment_id, workspace_id=ws_id)
    try:
        data = att_svc.read_bytes(att)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "attachment.blob_missing", "message": str(e)},
        ) from e
    try:
        result = await svc.ingest_attachment(
            db,
            collection=col,
            attachment=att,
            data=data,
            created_by=identity_id,
            title_override=body.title,
        )
    except svc.AttachmentExtractError as e:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": e.code, "message": str(e)},
        ) from e
    await audit_svc.record(
        db,
        action="knowledge.doc.ingest_from_attachment",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="doc",
        resource_id=result.doc.id,
        summary=(f"ingested {att.filename!r} → {result.chunks} chunks (collection {col.name!r})"),
        metadata={
            "attachment_id": str(att.id),
            "chunks": result.chunks,
            "mime_type": att.mime_type,
        },
        request=request,
    )
    await db.commit()
    return KnowledgeDocRead.model_validate(result.doc)


@router.delete(
    "/collections/{collection_id}/docs/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_doc(
    collection_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    doc = await AsyncRepository(db, KnowledgeDoc).get(doc_id)
    if doc is None or doc.collection_id != collection_id:
        return
    await AsyncRepository(db, KnowledgeDoc).soft_delete(doc)
    # Also hard-delete chunks so they stop showing up in search.
    from sqlalchemy import delete

    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
    await db.commit()


# ─── Search ───────────────────────────────────────────────
@router.post(
    "/collections/{collection_id}/search",
    response_model=list[KnowledgeChunkHit],
)
async def search_collection(
    collection_id: uuid.UUID,
    body: KnowledgeSearchIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[KnowledgeChunkHit]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    col = await svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    allowed = await kb_src_svc.filter_accessible_doc_ids(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        collection_id=col.id,
    )
    hits = await svc.search(
        db,
        collection=col,
        query=body.query,
        top_k=body.top_k,
        allowed_doc_ids=allowed,
    )
    return [
        KnowledgeChunkHit(
            id=h.id,
            doc_id=h.doc_id,
            doc_title=h.doc_title,
            ord=h.ord,
            text=h.text,
            score=h.score,
        )
        for h in hits
    ]
