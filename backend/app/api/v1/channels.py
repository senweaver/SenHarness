"""Channel CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.repositories.channel import ChannelRepository
from app.schemas.channel import ChannelCreate, ChannelRead, ChannelUpdate
from app.services import audit as audit_svc
from app.services import channel as svc
from app.services import workspace as ws_svc
from app.services.channel import mask_config
from app.services.channels import describe_providers

router = APIRouter(prefix="/channels", tags=["channels"])


# ─── Provider discovery (public) ─────────────────────────
@router.get("/kinds", summary="List installable channel providers")
async def list_kinds() -> dict:
    """Enumerate every channel provider registered in this deployment.

    Drives the Channel-create form's provider picker and the
    required-config-fields hint list. Public because the set of
    providers isn't sensitive — operators typically surface the same
    info in sales / onboarding docs.

    Shape is stable: ``kind`` + ``display_name`` + ``description`` +
    ``docs_url`` + ``required_config_fields`` +
    ``optional_config_fields`` + ``supports_outbound``. Changing it
    breaks the frontend form in lockstep.
    """
    providers = describe_providers()
    return {"providers": providers, "count": len(providers)}


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _present(ch) -> ChannelRead:
    card = ChannelRead.model_validate(ch)
    # Don't leak bot tokens / signing secrets via REST — the inbound_token is
    # meant to be shared with the IM provider so it stays visible.
    card.config_json = mask_config(ch.config_json or {})
    return card


@router.get("", response_model=list[ChannelRead])
async def list_channels(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ChannelRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await ChannelRepository(db).list_for_workspace(workspace_id=ws_id)
    return [_present(r) for r in rows]


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.create_channel(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        name=body.name,
        kind=body.kind,
        config_json=body.config_json,
        default_agent_id=body.default_agent_id,
        default_squad_id=body.default_squad_id,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
    )
    await audit_svc.record(
        db,
        action="channel.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"created channel {ch.name!r} ({ch.kind})",
        metadata={"kind": ch.kind},
        request=request,
    )
    await db.commit()
    return _present(ch)


@router.get("/{channel_id}", response_model=ChannelRead)
async def get_channel(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    return _present(ch)


@router.patch("/{channel_id}", response_model=ChannelRead)
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)

    # Merge config_json instead of replacing so editing one key from the UI
    # doesn't wipe out bot_token etc.
    patch = body.model_dump(exclude_none=True)
    if "config_json" in patch:
        merged = dict(ch.config_json or {})
        for k, v in (patch["config_json"] or {}).items():
            # Don't let masked sentinels overwrite the real stored value.
            if isinstance(v, str) and v.startswith("•••"):
                continue
            merged[k] = v
        patch["config_json"] = merged

    ch = await ChannelRepository(db).update(ch, **patch)
    await audit_svc.record(
        db,
        action="channel.update",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"updated channel {ch.name!r}",
        metadata={"fields": sorted(patch.keys())},
        request=request,
    )
    await db.commit()
    return _present(ch)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    await ChannelRepository(db).soft_delete(ch)
    await audit_svc.record(
        db,
        action="channel.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"deleted channel {ch.name!r}",
        request=request,
    )
    await db.commit()


@router.post("/{channel_id}/rotate-token", response_model=ChannelRead)
async def rotate_token(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    ch = await svc.rotate_token(db, channel=ch)
    await audit_svc.record(
        db,
        action="channel.rotate_token",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"rotated inbound token for {ch.name!r}",
        request=request,
    )
    await db.commit()
    return _present(ch)


