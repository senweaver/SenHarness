"""Knowledge-base source connectors + document ACL endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.kb_source import KbSource
from app.db.repository import AsyncRepository
from app.schemas.kb_source import (
    KbAccessCreate,
    KbAccessRead,
    KbConnectorInfo,
    KbSourceCreate,
    KbSourceRead,
    KbSourceUpdate,
    KbSyncRead,
)
from app.services import audit as audit_svc
from app.services import kb_source as svc
from app.services import knowledge as knowledge_svc
from app.services import workspace as ws_svc
from app.services.kb_connectors import describe_connectors, get_connector

router = APIRouter(prefix="/kb", tags=["kb"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Connector catalog ────────────────────────────────────
@router.get("/connectors", response_model=list[KbConnectorInfo])
async def list_connectors() -> list[KbConnectorInfo]:
    """Return every registered connector + its config schema.

    Consumed by the /settings/knowledge form's "add source" picker so the
    UI renders without hard-coding a connector list.
    """
    return [KbConnectorInfo.model_validate(d) for d in describe_connectors()]


# ─── Source CRUD ──────────────────────────────────────────
@router.get("/collections/{collection_id}/sources", response_model=list[KbSourceRead])
async def list_sources(
    collection_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[KbSourceRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await knowledge_svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    rows = await svc.list_sources_for_collection(
        db, workspace_id=ws_id, collection_id=collection_id
    )
    return [KbSourceRead.model_validate(r) for r in rows]


@router.post("/sources", response_model=KbSourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: KbSourceCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> KbSourceRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    col = await knowledge_svc.get_collection_or_404(db, body.collection_id, workspace_id=ws_id)
    try:
        connector = get_connector(body.kind)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "kb.unknown_connector", "message": str(exc)},
        ) from exc
    try:
        connector.validate_config(body.config_json)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "kb.invalid_config", "message": str(exc)},
        ) from exc

    row = await AsyncRepository(db, KbSource).create(
        workspace_id=ws_id,
        collection_id=col.id,
        name=body.name,
        kind=body.kind,
        config_json=body.config_json,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="kb.source.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="kb_source",
        resource_id=row.id,
        summary=f"created kb source {body.name!r} (kind={body.kind})",
        request=request,
    )
    await db.commit()
    return KbSourceRead.model_validate(row)


@router.patch("/sources/{source_id}", response_model=KbSourceRead)
async def update_source(
    source_id: uuid.UUID,
    body: KbSourceUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> KbSourceRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.get_source_or_404(db, source_id, workspace_id=ws_id)
    patch = body.model_dump(exclude_none=True)
    if "kind" in patch or "config_json" in patch:
        target_kind = patch.get("kind", row.kind)
        target_config = patch.get("config_json", row.config_json)
        try:
            connector = get_connector(target_kind)
            connector.validate_config(target_config or {})
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "kb.unknown_connector", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "kb.invalid_config", "message": str(exc)},
            ) from exc
    row = await AsyncRepository(db, KbSource).update(row, **patch)
    await db.commit()
    return KbSourceRead.model_validate(row)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.get_source_or_404(db, source_id, workspace_id=ws_id)
    await AsyncRepository(db, KbSource).soft_delete(row)
    await audit_svc.record(
        db,
        action="kb.source.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="kb_source",
        resource_id=row.id,
        summary=f"deleted kb source {row.name!r}",
        request=request,
    )
    await db.commit()


# ─── Sync ─────────────────────────────────────────────────
@router.post(
    "/sources/{source_id}/sync",
    response_model=KbSyncRead,
    status_code=status.HTTP_200_OK,
    summary="Run a sync pass and return the final sync row (blocking).",
)
async def run_sync_blocking(
    source_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> KbSyncRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    source = await svc.get_source_or_404(db, source_id, workspace_id=ws_id)
    col = await knowledge_svc.get_collection_or_404(db, source.collection_id, workspace_id=ws_id)
    final_sync_id: uuid.UUID | None = None
    async for update in svc.run_sync(db, source=source, collection=col, started_by=identity_id):
        if update.kind in {"started", "done", "failed"}:
            raw = update.payload.get("sync_id")
            if raw:
                final_sync_id = uuid.UUID(raw)
    from app.db.models.kb_source import KbSourceSync

    row = await AsyncRepository(db, KbSourceSync).get(final_sync_id) if final_sync_id else None
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "kb.sync_failed", "message": "sync produced no row"},
        )
    return KbSyncRead.model_validate(row)


@router.get(
    "/sources/{source_id}/sync/stream",
    summary="Run a sync pass and stream progress as Server-Sent Events.",
)
async def run_sync_sse(
    source_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> StreamingResponse:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    source = await svc.get_source_or_404(db, source_id, workspace_id=ws_id)
    col = await knowledge_svc.get_collection_or_404(db, source.collection_id, workspace_id=ws_id)
    generator = svc.stream_sse(
        svc.run_sync(db, source=source, collection=col, started_by=identity_id)
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/sources/{source_id}/syncs",
    response_model=list[KbSyncRead],
)
async def list_syncs(
    source_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = 50,
) -> list[KbSyncRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    source = await svc.get_source_or_404(db, source_id, workspace_id=ws_id)
    from sqlalchemy import desc, select

    from app.db.models.kb_source import KbSourceSync

    stmt = (
        select(KbSourceSync)
        .where(KbSourceSync.source_id == source.id)
        .order_by(desc(KbSourceSync.created_at))
        .limit(max(1, min(limit, 200)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [KbSyncRead.model_validate(r) for r in rows]


# ─── Document ACL ────────────────────────────────────────
@router.get("/collections/{collection_id}/access", response_model=list[KbAccessRead])
async def list_access(
    collection_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[KbAccessRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await knowledge_svc.get_collection_or_404(db, collection_id, workspace_id=ws_id)
    rows = await svc.list_access_entries(db, collection_id=collection_id)
    return [KbAccessRead.model_validate(r) for r in rows]


@router.post(
    "/access",
    response_model=KbAccessRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_access(
    body: KbAccessCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> KbAccessRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await knowledge_svc.get_collection_or_404(db, body.collection_id, workspace_id=ws_id)
    row = await svc.grant_access(
        db,
        workspace_id=ws_id,
        collection_id=body.collection_id,
        doc_id=body.doc_id,
        subject_kind=body.subject_kind,
        subject_id=body.subject_id,
        level=body.level,
        granted_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="kb.access.grant",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="kb_access",
        resource_id=row.id,
        summary=(
            f"granted {body.level.value} to {body.subject_kind.value}={body.subject_id} "
            f"on collection={body.collection_id}"
        ),
        metadata={
            "doc_id": str(body.doc_id) if body.doc_id else None,
            "subject_kind": body.subject_kind.value,
            "subject_id": str(body.subject_id),
            "level": body.level.value,
        },
        request=request,
    )
    await db.commit()
    return KbAccessRead.model_validate(row)


@router.delete("/access/{access_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_access(
    access_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.revoke_access(db, access_id=access_id, workspace_id=ws_id)
    await audit_svc.record(
        db,
        action="kb.access.revoke",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="kb_access",
        resource_id=access_id,
        summary=f"revoked kb access {access_id}",
        request=request,
    )
    await db.commit()
