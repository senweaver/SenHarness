"""Attachment upload + download endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.schemas.attachment import AttachmentRead
from app.services import attachment as svc
from app.services import audit as audit_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.post(
    "",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    file: UploadFile = File(...),
    session_id: uuid.UUID | None = Form(None),
) -> AttachmentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)

    # Read the full body — chunked streaming isn't needed at 25MB ceiling.
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty_file"
        )

    att = await svc.store_bytes(
        db,
        workspace_id=ws_id,
        uploader_identity_id=identity_id,
        filename=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        data=data,
        session_id=session_id,
    )
    await audit_svc.record(
        db,
        action="attachment.upload",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="attachment",
        resource_id=att.id,
        summary=f"uploaded {att.filename!r} ({att.size_bytes} bytes, {att.mime_type})",
        metadata={
            "filename": att.filename,
            "mime": att.mime_type,
            "size": att.size_bytes,
            "kind": att.kind,
        },
        request=request,
    )
    await db.commit()
    return AttachmentRead.model_validate(att)


@router.get("/{attachment_id}", response_model=AttachmentRead)
async def get_attachment(
    attachment_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AttachmentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    att = await svc.get_for_read(db, attachment_id=attachment_id, workspace_id=ws_id)
    return AttachmentRead.model_validate(att)


@router.get("/{attachment_id}/content")
async def download_attachment(
    attachment_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> Response:
    """Stream the raw bytes. Content-Type = original mime.

    Workspace admins & members can download any attachment bound to the
    workspace (same trust boundary as the chat itself).
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    att = await svc.get_for_read(db, attachment_id=attachment_id, workspace_id=ws_id)
    data = svc.read_bytes(att)
    # Inline for images (browsers render in <img>); other kinds trigger a
    # download dialog. Filename ASCII-escaped to avoid header issues.
    dispo = "inline" if att.kind == "image" else "attachment"
    safe_name = att.filename.encode("ascii", errors="replace").decode("ascii")
    return Response(
        content=data,
        media_type=att.mime_type,
        headers={
            "Content-Disposition": f'{dispo}; filename="{safe_name}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete(
    "/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    attachment_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    att = await svc.get_for_read(db, attachment_id=attachment_id, workspace_id=ws_id)
    # Only the uploader or a workspace admin can delete; unclear ownership
    # would let anyone nuke anyone's upload.
    if att.uploader_identity_id and att.uploader_identity_id != identity_id:
        await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.soft_delete(db, attachment=att)
    await db.commit()
